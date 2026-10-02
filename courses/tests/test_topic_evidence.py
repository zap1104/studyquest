from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from courses.models import Course, Chapter, Quiz, Question, Choice, CourseTopic, QuestionTopic, QuizAttempt
from courses.topic_services import (
    create_course_topic,
    determine_evidence_state,
    get_course_topic_progress,
    get_course_topics_evidence_map,
    get_topic_evidence,
    link_question_to_topic,
)
from dungeon.models import DungeonRun


class TopicEvidenceTests(TestCase):
    """Test suite for Phase TE-2: Topic Evidence Aggregation Engine."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="evidence_student", password="test-password-123"
        )
        self.course = Course.objects.create(
            user=self.user,
            title="Software Architecture & Engineering",
            description="Testing TE-2 Evidence Aggregation",
        )
        self.chapter = Chapter.objects.create(
            course=self.course, title="Module 1: Architecture Core", order=1
        )
        self.quiz = Quiz.objects.create(chapter=self.chapter, title="Module 1 Quiz")

        # Create topic
        self.topic = create_course_topic(
            course=self.course,
            key="bdat-architecture",
            name="BDAT Architecture",
            description="Core enterprise architecture domains",
            order=1,
        )

    def _create_question(self, order, text="Question text"):
        q = Question.objects.create(
            quiz=self.quiz,
            order=order,
            question_type="multiple_choice",
            text=f"{text} {order}",
            explanation=f"Explanation for question {order}.",
        )
        Choice.objects.create(question=q, text="Correct", is_correct=True)
        Choice.objects.create(question=q, text="Incorrect", is_correct=False)
        link_question_to_topic(question=q, topic=self.topic)
        return q

    # -------------------------------------------------------------------------
    # 1. Pure State Determination Logic Tests
    # -------------------------------------------------------------------------
    def test_determine_evidence_state_transitions(self):
        # 1. Not Assessed
        state, expl = determine_evidence_state(
            unique_questions_assessed=0, accuracy=0.0, unresolved_count=0, correct_count=0
        )
        self.assertEqual(state, "Not Assessed")
        self.assertIn("No questions attempted yet", expl)

        # 2. Early Evidence (1-2 questions)
        state, expl = determine_evidence_state(
            unique_questions_assessed=1, accuracy=100.0, unresolved_count=0, correct_count=1
        )
        self.assertEqual(state, "Early Evidence")
        self.assertIn("More practice needed", expl)

        state, _ = determine_evidence_state(
            unique_questions_assessed=2, accuracy=50.0, unresolved_count=1, correct_count=1
        )
        self.assertEqual(state, "Early Evidence")

        # 3. Needs Attention (< 60% accuracy OR >= 2 unresolved mistakes)
        state, _ = determine_evidence_state(
            unique_questions_assessed=3, accuracy=50.0, unresolved_count=1, correct_count=1
        )
        self.assertEqual(state, "Needs Attention")

        state, _ = determine_evidence_state(
            unique_questions_assessed=4, accuracy=75.0, unresolved_count=2, correct_count=2
        )
        self.assertEqual(state, "Needs Attention")

        # 4. Developing (60-79% accuracy, < 2 unresolved)
        state, _ = determine_evidence_state(
            unique_questions_assessed=3, accuracy=66.7, unresolved_count=1, correct_count=2
        )
        self.assertEqual(state, "Developing")

        state, _ = determine_evidence_state(
            unique_questions_assessed=4, accuracy=75.0, unresolved_count=1, correct_count=3
        )
        self.assertEqual(state, "Developing")

        # 5. Showing Progress (>= 80% accuracy, but unresolved mistakes remain OR < 5 questions)
        state, _ = determine_evidence_state(
            unique_questions_assessed=5, accuracy=80.0, unresolved_count=1, correct_count=4
        )
        self.assertEqual(state, "Showing Progress")

        state, _ = determine_evidence_state(
            unique_questions_assessed=3, accuracy=100.0, unresolved_count=0, correct_count=3
        )
        self.assertEqual(state, "Showing Progress")

        # 6. Consistently Demonstrated (>= 5 questions, >= 85% accuracy, 0 unresolved)
        state, expl = determine_evidence_state(
            unique_questions_assessed=5, accuracy=85.0, unresolved_count=0, correct_count=5
        )
        self.assertEqual(state, "Consistently Demonstrated")
        self.assertIn("consistently answered correctly", expl)

        state, _ = determine_evidence_state(
            unique_questions_assessed=7, accuracy=100.0, unresolved_count=0, correct_count=7
        )
        self.assertEqual(state, "Consistently Demonstrated")

    # -------------------------------------------------------------------------
    # 2. Database Integration: Not Assessed
    # -------------------------------------------------------------------------
    def test_topic_with_no_attempts_is_not_assessed(self):
        self._create_question(order=1)
        evidence = get_topic_evidence(self.user, self.topic)

        self.assertEqual(evidence["unique_questions_assessed"], 0)
        self.assertEqual(evidence["total_answer_events"], 0)
        self.assertEqual(evidence["correct_count"], 0)
        self.assertEqual(evidence["partial_count"], 0)
        self.assertEqual(evidence["incorrect_count"], 0)
        self.assertEqual(evidence["unresolved_count"], 0)
        self.assertEqual(evidence["accuracy"], 0.0)
        self.assertIsNone(evidence["latest_activity_at"])
        self.assertEqual(evidence["evidence_state"], "Not Assessed")

    # -------------------------------------------------------------------------
    # 3. Retakes & Latest Known Question State
    # -------------------------------------------------------------------------
    def test_retake_uses_latest_known_outcome_per_question(self):
        """Repeated attempts on the same question update its state without inflating unique count."""
        q1 = self._create_question(order=1)
        now = timezone.now()

        # Attempt 1: Failed Q1
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz,
            score=0,
            total_questions=1,
            completed_at=now - timedelta(hours=2),
            review_data={
                "review_items": [
                    {
                        "question_id": q1.id,
                        "earned_points": 0.0,
                        "maximum_points": 1.0,
                        "is_correct": False,
                    }
                ]
            },
        )

        evidence_after_att1 = get_topic_evidence(self.user, self.topic)
        self.assertEqual(evidence_after_att1["unique_questions_assessed"], 1)
        self.assertEqual(evidence_after_att1["total_answer_events"], 1)
        self.assertEqual(evidence_after_att1["incorrect_count"], 1)
        self.assertEqual(evidence_after_att1["unresolved_count"], 1)
        self.assertEqual(evidence_after_att1["evidence_state"], "Early Evidence")

        # Attempt 2: Answered Q1 Correctly
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz,
            score=1,
            total_questions=1,
            completed_at=now - timedelta(hours=1),
            review_data={
                "review_items": [
                    {
                        "question_id": q1.id,
                        "earned_points": 1.0,
                        "maximum_points": 1.0,
                        "is_correct": True,
                    }
                ]
            },
        )

        evidence_after_att2 = get_topic_evidence(self.user, self.topic)
        # Unique questions remains 1, but total events is 2
        self.assertEqual(evidence_after_att2["unique_questions_assessed"], 1)
        self.assertEqual(evidence_after_att2["total_answer_events"], 2)
        # Latest state is resolved/correct
        self.assertEqual(evidence_after_att2["correct_count"], 1)
        self.assertEqual(evidence_after_att2["incorrect_count"], 0)
        self.assertEqual(evidence_after_att2["unresolved_count"], 0)
        self.assertEqual(evidence_after_att2["accuracy"], 100.0)

    # -------------------------------------------------------------------------
    # 4. Partial Credit Handling
    # -------------------------------------------------------------------------
    def test_partial_credit_uses_earned_points_ratio(self):
        """Partial answers count under partial_count and contribute proportional ratio."""
        q1 = self._create_question(order=1)
        q2 = self._create_question(order=2)
        q3 = self._create_question(order=3)

        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz,
            score=2.5,
            total_questions=3,
            completed_at=timezone.now(),
            review_data={
                "review_items": [
                    {"question_id": q1.id, "earned_points": 1.0, "maximum_points": 1.0, "is_correct": True},
                    {"question_id": q2.id, "earned_points": 1.0, "maximum_points": 1.0, "is_correct": True},
                    # Partial: 0.5 out of 1.0
                    {"question_id": q3.id, "earned_points": 0.5, "maximum_points": 1.0, "is_correct": False},
                ]
            },
        )

        evidence = get_topic_evidence(self.user, self.topic)
        self.assertEqual(evidence["unique_questions_assessed"], 3)
        self.assertEqual(evidence["correct_count"], 2)
        self.assertEqual(evidence["partial_count"], 1)
        self.assertEqual(evidence["incorrect_count"], 0)
        self.assertEqual(evidence["unresolved_count"], 1)

        # Accuracy = (1.0 + 1.0 + 0.5) / 3 * 100 = 83.3%
        self.assertEqual(evidence["accuracy"], 83.3)
        # 3 questions, 83.3% accuracy, 1 unresolved mistake -> Showing Progress
        self.assertEqual(evidence["evidence_state"], "Showing Progress")

    # -------------------------------------------------------------------------
    # 5. Dungeon Review Runs Resolve Mistakes
    # -------------------------------------------------------------------------
    def test_completed_review_run_resolves_mistake(self):
        """A completed Dungeon Review Run marks targeted mastered questions as correct and resolved."""
        q1 = self._create_question(order=1)
        now = timezone.now()

        # Quiz Attempt: Q1 was wrong
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz,
            score=0,
            total_questions=1,
            review_data={
                "review_items": [
                    {"question_id": q1.id, "earned_points": 0.0, "maximum_points": 1.0, "is_correct": False}
                ]
            },
        )

        # Finished Review Run: executed after the quiz attempt and mastered Q1
        DungeonRun.objects.create(
            user=self.user,
            quiz=self.quiz,
            run_type=DungeonRun.TYPE_REVIEW,
            status=DungeonRun.STATUS_CLEARED,
            review_question_ids=[q1.id],
            review_mastered_question_ids=[q1.id],
            finished_at=now + timedelta(minutes=10),
        )

        evidence = get_topic_evidence(self.user, self.topic)
        self.assertEqual(evidence["unique_questions_assessed"], 1)
        self.assertEqual(evidence["total_answer_events"], 2)
        self.assertEqual(evidence["correct_count"], 1)
        self.assertEqual(evidence["unresolved_count"], 0)
        self.assertEqual(evidence["accuracy"], 100.0)

    # -------------------------------------------------------------------------
    # 6. Classic Dungeon Is Strictly Excluded
    # -------------------------------------------------------------------------
    def test_classic_dungeon_runs_do_not_contribute_evidence(self):
        """Classic Dungeon expeditions must never alter topic evidence counts."""
        q1 = self._create_question(order=1)

        # Create a finished Classic Dungeon run
        DungeonRun.objects.create(
            user=self.user,
            quiz=self.quiz,
            run_type=DungeonRun.TYPE_CLASSIC,
            status=DungeonRun.STATUS_CLEARED,
            current_hp=50,
            max_hp=50,
            review_question_ids=[q1.id],
            review_mastered_question_ids=[q1.id],
            finished_at=timezone.now(),
        )

        evidence = get_topic_evidence(self.user, self.topic)
        # Classic run did not contribute any answer events or assessed questions
        self.assertEqual(evidence["unique_questions_assessed"], 0)
        self.assertEqual(evidence["total_answer_events"], 0)
        self.assertEqual(evidence["evidence_state"], "Not Assessed")

    # -------------------------------------------------------------------------
    # 7. Consistently Demonstrated Threshold (5 questions, >= 85%, 0 unresolved)
    # -------------------------------------------------------------------------
    def test_consistently_demonstrated_reached(self):
        questions = [self._create_question(order=i) for i in range(1, 6)]
        review_items = [
            {"question_id": q.id, "earned_points": 1.0, "maximum_points": 1.0, "is_correct": True}
            for q in questions
        ]

        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz,
            score=5.0,
            total_questions=5,
            completed_at=timezone.now(),
            review_data={"review_items": review_items},
        )

        evidence = get_topic_evidence(self.user, self.topic)
        self.assertEqual(evidence["unique_questions_assessed"], 5)
        self.assertEqual(evidence["correct_count"], 5)
        self.assertEqual(evidence["unresolved_count"], 0)
        self.assertEqual(evidence["accuracy"], 100.0)
        self.assertEqual(evidence["evidence_state"], "Consistently Demonstrated")

    # -------------------------------------------------------------------------
    # 8. Legacy Attempts Without review_items
    # -------------------------------------------------------------------------
    def test_legacy_attempts_without_review_items_are_gracefully_ignored(self):
        """Attempts lacking review_items are treated as unavailable evidence, not failures."""
        self._create_question(order=1)

        # Legacy attempt without review_items
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz,
            score=5.0,
            total_questions=10,
            completed_at=timezone.now(),
            review_data={"percentage": 50, "passed": False},
        )

        evidence = get_topic_evidence(self.user, self.topic)
        self.assertEqual(evidence["unique_questions_assessed"], 0)
        self.assertEqual(evidence["total_answer_events"], 0)
        self.assertEqual(evidence["evidence_state"], "Not Assessed")

    # -------------------------------------------------------------------------
    # 9. Course Topic Progress Coverage & State Counts
    # -------------------------------------------------------------------------
    def test_get_course_topic_progress_aggregation(self):
        """Tests full course aggregation across multiple topics."""
        t2 = create_course_topic(
            course=self.course,
            key="togaf-framework",
            name="TOGAF Framework",
            order=2,
        )
        t3 = create_course_topic(
            course=self.course,
            key="zachman-matrix",
            name="Zachman Matrix",
            order=3,
        )

        # Topic 1 gets 5 correct questions (Consistently Demonstrated)
        q_t1 = [self._create_question(order=i) for i in range(1, 6)]
        items_t1 = [
            {"question_id": q.id, "earned_points": 1.0, "maximum_points": 1.0, "is_correct": True}
            for q in q_t1
        ]

        # Topic 2 gets 1 question (Early Evidence)
        q_t2 = Question.objects.create(
            quiz=self.quiz, order=10, question_type="multiple_choice", text="TOGAF Q1"
        )
        Choice.objects.create(question=q_t2, text="Correct", is_correct=True)
        link_question_to_topic(question=q_t2, topic=t2)
        items_t2 = [
            {"question_id": q_t2.id, "earned_points": 1.0, "maximum_points": 1.0, "is_correct": True}
        ]

        # Topic 3 gets 0 questions

        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz,
            score=6.0,
            total_questions=6,
            completed_at=timezone.now(),
            review_data={"review_items": items_t1 + items_t2},
        )

        progress = get_course_topic_progress(self.user, self.course)

        self.assertTrue(progress["has_topics"])
        self.assertEqual(progress["total_topics"], 3)
        self.assertEqual(progress["assessed_topics_count"], 2)
        # 2 / 3 * 100 = 67%
        self.assertEqual(progress["coverage_percentage"], 67)

        state_counts = progress["state_counts"]
        self.assertEqual(state_counts["Consistently Demonstrated"], 1)
        self.assertEqual(state_counts["Early Evidence"], 1)
        self.assertEqual(state_counts["Not Assessed"], 1)
        self.assertEqual(state_counts["Needs Attention"], 0)

    # -------------------------------------------------------------------------
    # 10. Legacy Course Without Topics Fallback
    # -------------------------------------------------------------------------
    def test_legacy_course_without_topics_fallback(self):
        empty_course = Course.objects.create(
            user=self.user, title="Legacy Course", description="No topics"
        )
        progress = get_course_topic_progress(self.user, empty_course)

        self.assertFalse(progress["has_topics"])
        self.assertEqual(progress["total_topics"], 0)
        self.assertEqual(progress["assessed_topics_count"], 0)
        self.assertEqual(progress["coverage_percentage"], 0)
        self.assertEqual(progress["topics"], [])
