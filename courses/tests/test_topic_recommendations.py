"""Tests for TE-3A Topic-Aware Recommendations Engine.

Verifies:
1. Priority 1 (Needs Attention) is chosen over Developing, Showing Progress, and Not Assessed.
2. Priority 2 (Developing) is chosen over Showing Progress and Not Assessed.
3. Priority 3 (Showing Progress with unresolved mistakes) is chosen over Not Assessed.
4. Priority 4 (Not Assessed / Early Evidence) is chosen when remaining topics are mastered.
5. Priority 5 (All topics demonstrated): Consistently Demonstrated topics are NEVER
   recommended for review; routes learners to Dungeon Quest expedition.
6. Clear observable evidence count phrasing without techy jargon.
7. Active Study Focus target topic prioritization in tie-breaking.
8. Backward compatibility for legacy courses without topics.
9. View rendering on focus summary and course detail pages.
"""

from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse

from courses.models import Course, Chapter, Quiz, Question, CourseTopic, QuestionTopic, QuizAttempt, LearningFocus
from courses.topic_services import (
    create_course_topic,
    link_question_to_topic,
    get_topic_recommendation,
)
from courses.learning_focus_service import (
    get_focus_remediation_recommendations,
    create_manual_learning_focus,
    link_focus_to_course,
)


class TopicRecommendationsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="learner1", password="password123")
        self.client = Client()
        self.client.login(username="learner1", password="password123")

        self.course = Course.objects.create(user=self.user, title="Technopreneurship 101")
        self.ch1 = Chapter.objects.create(course=self.course, order=1, title="Chapter 1: Ideation")
        self.ch2 = Chapter.objects.create(course=self.course, order=2, title="Chapter 2: Validation")
        self.quiz1 = Quiz.objects.create(chapter=self.ch1, title="Quiz 1")
        self.quiz2 = Quiz.objects.create(chapter=self.ch2, title="Quiz 2")

        # Create topics
        self.topic_arch = create_course_topic(
            course=self.course, key="bdat-architecture", name="BDAT Architecture", order=1
        )
        self.topic_val = create_course_topic(
            course=self.course, key="market-validation", name="Market Validation", order=2
        )
        self.topic_fin = create_course_topic(
            course=self.course, key="financial-modeling", name="Financial Modeling", order=3
        )

    def _create_questions(self, quiz, topic, count):
        questions = []
        for i in range(count):
            q = Question.objects.create(
                quiz=quiz,
                text=f"Question {i+1} for {topic.name}",
                explanation="Explanation",
            )
            link_question_to_topic(question=q, topic=topic)
            questions.append(q)
        return questions

    def test_priority_1_needs_attention_chosen_over_developing(self):
        """Priority 1: Needs Attention takes precedence over Developing and Not Assessed."""
        # Topic Arch: 3 questions, 1 correct, 2 incorrect -> Needs Attention (unresolved=2, acc=33.3%)
        q_arch = self._create_questions(self.quiz1, self.topic_arch, 3)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz1,
            score=1.0,
            total_questions=3,
            review_data={
                "percentage": 33.3,
                "review_items": [
                    {"question_id": q_arch[0].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_arch[1].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                    {"question_id": q_arch[2].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                ],
            },
        )

        # Topic Val: 3 questions, 2 correct, 1 incorrect -> Developing (unresolved=1, acc=66.7%)
        q_val = self._create_questions(self.quiz2, self.topic_val, 3)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz2,
            score=2.0,
            total_questions=3,
            review_data={
                "percentage": 66.7,
                "review_items": [
                    {"question_id": q_val[0].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_val[1].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_val[2].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                ],
            },
        )

        # Topic Fin is Not Assessed (0 questions)

        rec = get_topic_recommendation(self.user, self.course)
        self.assertIsNotNone(rec)
        self.assertTrue(rec["has_recommendation"])
        self.assertEqual(rec["priority_level"], 1)
        self.assertEqual(rec["topic_name"], "BDAT Architecture")
        self.assertEqual(rec["badge_label"], "RECOMMENDED FOR REVIEW")
        self.assertEqual(rec["action_type"], "topic_review")
        self.assertEqual(rec["button_label"], "Open Chapter Review →")
        self.assertEqual(rec["url"], f"/chapters/{self.ch1.id}/review/")
        self.assertEqual(rec["secondary_button_label"], "Start Review Run →")
        self.assertEqual(rec["secondary_url"], "/dungeon/")
        self.assertIn("2 questions need attention. 2 mistakes remain unresolved.", rec["reason"])

    def test_priority_2_developing_chosen_over_showing_progress_and_unassessed(self):
        """Priority 2: Developing takes precedence over cleanly Showing Progress and Not Assessed."""
        # Topic Val: 3 questions, 2 correct, 1 incorrect -> Developing (unresolved=1, acc=66.7%)
        q_val = self._create_questions(self.quiz2, self.topic_val, 3)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz2,
            score=2.0,
            total_questions=3,
            review_data={
                "percentage": 66.7,
                "review_items": [
                    {"question_id": q_val[0].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_val[1].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_val[2].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                ],
            },
        )

        # Topic Arch: 3 questions, 3 correct -> Showing Progress (0 unresolved, 100%)
        q_arch = self._create_questions(self.quiz1, self.topic_arch, 3)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz1,
            score=3.0,
            total_questions=3,
            review_data={
                "percentage": 100.0,
                "review_items": [
                    {"question_id": q_arch[0].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_arch[1].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_arch[2].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                ],
            },
        )

        rec = get_topic_recommendation(self.user, self.course)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["priority_level"], 2)
        self.assertEqual(rec["topic_name"], "Market Validation")
        self.assertEqual(rec["badge_label"], "RECOMMENDED FOR REVIEW")
        self.assertEqual(rec["button_label"], "Open Chapter Review →")
        self.assertEqual(rec["url"], f"/chapters/{self.ch2.id}/review/")
        self.assertEqual(rec["secondary_button_label"], "Start Review Run →")
        self.assertEqual(rec["secondary_url"], "/dungeon/")

    def test_priority_3_showing_progress_with_unresolved_chosen_over_unassessed(self):
        """Priority 3: Showing Progress with unresolved mistakes takes precedence over Not Assessed."""
        # Topic Arch: 4 questions, 3 correct, 1 incorrect (acc=75%, unresolved=1 -> wait, in TE-2:
        # acc=75% is Developing! Let's make acc=80% with 5 questions: 4 correct, 1 incorrect = 80%, unresolved=1 -> Showing Progress)
        q_arch = self._create_questions(self.quiz1, self.topic_arch, 5)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz1,
            score=4.0,
            total_questions=5,
            review_data={
                "percentage": 80.0,
                "review_items": [
                    {"question_id": q_arch[0].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_arch[1].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_arch[2].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_arch[3].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_arch[4].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                ],
            },
        )

        # Topic Val & Topic Fin are Not Assessed
        rec = get_topic_recommendation(self.user, self.course)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["priority_level"], 3)
        self.assertEqual(rec["topic_name"], "BDAT Architecture")
        self.assertEqual(rec["badge_label"], "RECOMMENDED FOR REVIEW")
        self.assertIn("4 of 5 questions answered correctly (80%), but 1 mistake remains to review.", rec["reason"])
        self.assertEqual(rec["button_label"], "Open Chapter Review →")
        self.assertEqual(rec["secondary_button_label"], "Start Review Run →")
        self.assertEqual(rec["secondary_url"], "/dungeon/")

    def test_priority_4_assessment_needed_when_remaining_topics_mastered(self):
        """Priority 4: Unassessed topics are recommended for assessment when other topics are mastered."""
        # Master Topic Arch (5 questions, all correct -> Consistently Demonstrated)
        q_arch = self._create_questions(self.quiz1, self.topic_arch, 5)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz1,
            score=5.0,
            total_questions=5,
            review_data={
                "percentage": 100.0,
                "review_items": [
                    {"question_id": q.id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0}
                    for q in q_arch
                ],
            },
        )

        # Master Topic Val (5 questions, all correct -> Consistently Demonstrated)
        q_val = self._create_questions(self.quiz2, self.topic_val, 5)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz2,
            score=5.0,
            total_questions=5,
            review_data={
                "percentage": 100.0,
                "review_items": [
                    {"question_id": q.id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0}
                    for q in q_val
                ],
            },
        )

        # Topic Fin is Not Assessed
        rec = get_topic_recommendation(self.user, self.course)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["priority_level"], 4)
        self.assertEqual(rec["topic_name"], "Financial Modeling")
        self.assertEqual(rec["badge_label"], "ASSESSMENT RECOMMENDED")
        self.assertEqual(rec["action_type"], "topic_assessment")
        self.assertIn("No questions assessed yet.", rec["reason"])
        self.assertEqual(rec["button_label"], "Open Chapter Review →")
        self.assertEqual(rec["secondary_button_label"], "Take Chapter Quiz →")

    def test_priority_5_consistently_demonstrated_topics_never_recommended_for_review(self):
        """Priority 5: When all topics are consistently demonstrated, none are recommended for review.

        Instead, the engine routes the learner to Dungeon Quest.
        """
        # Delete topic_fin so only topic_arch and topic_val remain
        self.topic_fin.delete()

        q_arch = self._create_questions(self.quiz1, self.topic_arch, 5)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz1,
            score=5.0,
            total_questions=5,
            review_data={
                "percentage": 100.0,
                "review_items": [
                    {"question_id": q.id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0}
                    for q in q_arch
                ],
            },
        )

        q_val = self._create_questions(self.quiz2, self.topic_val, 5)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz2,
            score=5.0,
            total_questions=5,
            review_data={
                "percentage": 100.0,
                "review_items": [
                    {"question_id": q.id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0}
                    for q in q_val
                ],
            },
        )

        rec = get_topic_recommendation(self.user, self.course)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["priority_level"], 5)
        self.assertIsNone(rec["topic_id"])
        self.assertEqual(rec["badge_label"], "MASTERY DEMONSTRATED")
        self.assertEqual(rec["title"], "Dungeon Expedition")
        self.assertEqual(rec["action_type"], "dungeon_quest")
        self.assertEqual(rec["button_label"], "Enter Dungeon Quest →")
        self.assertEqual(rec["url"], "/dungeon/")
        self.assertIsNone(rec["secondary_button_label"])

    def test_observable_evidence_counts_phrasing(self):
        """Verifies exact observable counts phrasing matching product specifications."""
        # 1 partial, 1 incorrect, 1 correct out of 3 questions
        q_arch = self._create_questions(self.quiz1, self.topic_arch, 3)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz1,
            score=1.5,
            total_questions=3,
            review_data={
                "percentage": 50.0,
                "review_items": [
                    {"question_id": q_arch[0].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_arch[1].id, "is_correct": False, "earned_points": 0.5, "maximum_points": 1.0},
                    {"question_id": q_arch[2].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                ],
            },
        )

        rec = get_topic_recommendation(self.user, self.course)
        self.assertEqual(rec["title"], "BDAT Architecture")
        # 2 questions need attention (1 partial, 1 incorrect), 1 mistake remains unresolved (the 0.0 point one)
        self.assertEqual(rec["reason"], "2 questions need attention. 1 mistake remains unresolved.")

    def test_active_study_focus_tie_breaking(self):
        """Tie-breaking within same priority level honors user's active Study Focus targets."""
        # Both Topic Arch and Topic Val are in Needs Attention
        q_arch = self._create_questions(self.quiz1, self.topic_arch, 3)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz1,
            score=1.0,
            total_questions=3,
            review_data={
                "percentage": 33.3,
                "review_items": [
                    {"question_id": q_arch[0].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_arch[1].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                    {"question_id": q_arch[2].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                ],
            },
        )

        q_val = self._create_questions(self.quiz2, self.topic_val, 3)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz2,
            score=1.0,
            total_questions=3,
            review_data={
                "percentage": 33.3,
                "review_items": [
                    {"question_id": q_val[0].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_val[1].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                    {"question_id": q_val[2].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                ],
            },
        )

        # Create Study Focus targeting Topic Val
        focus = create_manual_learning_focus(
            self.user,
            subject_name="Market Strategy",
            topic_names=["Market Validation"],
        )
        link_focus_to_course(focus, self.course)

        rec = get_topic_recommendation(self.user, self.course, focus=focus)
        self.assertEqual(rec["topic_name"], "Market Validation")

    def test_backward_compatibility_legacy_course_without_topics(self):
        """Legacy course without CourseTopic records uses chapter-level fallback without error."""
        legacy_course = Course.objects.create(user=self.user, title="Legacy Biology")
        ch = Chapter.objects.create(course=legacy_course, order=1, title="Cell Division")
        Quiz.objects.create(chapter=ch, title="Cell Quiz")

        focus = create_manual_learning_focus(
            self.user,
            subject_name="Cell Division",
            topic_names=["Cell"],
        )
        link_focus_to_course(focus, legacy_course)

        rec = get_focus_remediation_recommendations(self.user, focus)
        self.assertTrue(rec["has_recommendation"])
        self.assertEqual(rec["action_type"], "chapter_review")
        self.assertFalse(rec.get("is_topic_aware", False))

        # Direct call to get_topic_recommendation on course with no topics returns None
        self.assertIsNone(get_topic_recommendation(self.user, legacy_course))

    def test_focus_summary_and_course_detail_views(self):
        """Views render topic-aware recommendations and actions properly."""
        q_arch = self._create_questions(self.quiz1, self.topic_arch, 3)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz1,
            score=1.0,
            total_questions=3,
            review_data={
                "percentage": 33.3,
                "review_items": [
                    {"question_id": q_arch[0].id, "is_correct": True, "earned_points": 1.0, "maximum_points": 1.0},
                    {"question_id": q_arch[1].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                    {"question_id": q_arch[2].id, "is_correct": False, "earned_points": 0.0, "maximum_points": 1.0},
                ],
            },
        )

        focus = create_manual_learning_focus(
            self.user,
            subject_name="Technopreneurship",
            topic_names=["BDAT Architecture"],
        )
        link_focus_to_course(focus, self.course)

        # 1. Course detail page
        response = self.client.get(reverse("courses:course_detail", args=[self.course.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RECOMMENDED FOR REVIEW")
        self.assertContains(response, "BDAT Architecture")
        self.assertContains(response, "Open Chapter Review")
        self.assertContains(response, "Start Review Run")

        # 2. Focus summary page
        response_summary = self.client.get(reverse("courses:focus_summary", args=[focus.id]))
        self.assertEqual(response_summary.status_code, 200)
        self.assertContains(response_summary, "RECOMMENDED FOR REVIEW")
        self.assertContains(response_summary, "BDAT Architecture")
        self.assertContains(response_summary, "Course Topic Progress")
