"""Tests for TE-3B Starting Knowledge Check.

Verifies:
1. Grounded in uploaded materials: selects 5-10 existing topic-tagged questions.
2. Strictly optional: learner can take or skip.
3. Zero grade impact: no QuizAttempt or chapter progression changes.
4. Offered automatically only once per course.
5. Never reappears automatically after completion or dismissal.
6. Topic evidence calculation & focus suggestions.
7. Explicit approval required before changing Study Focus.
8. Complete views and routing integration.
"""

from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse

from courses.models import Course, Chapter, Quiz, Question, Choice, CourseTopic, StartingKnowledgeCheck, LearningFocus, QuizAttempt
from courses.topic_services import create_course_topic, link_question_to_topic
from courses.knowledge_check_services import (
    select_knowledge_check_questions,
    get_or_create_knowledge_check,
    grade_knowledge_check,
    adopt_knowledge_check_focus,
)


class StartingKnowledgeCheckTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="learner2", password="password123")
        self.client = Client()
        self.client.login(username="learner2", password="password123")

        self.course = Course.objects.create(user=self.user, title="Data Science 101")
        self.ch1 = Chapter.objects.create(course=self.course, order=1, title="Chapter 1: Python Basics")
        self.ch2 = Chapter.objects.create(course=self.course, order=2, title="Chapter 2: Pandas & NumPy")
        self.quiz1 = Quiz.objects.create(chapter=self.ch1, title="Quiz 1")
        self.quiz2 = Quiz.objects.create(chapter=self.ch2, title="Quiz 2")

        self.topic1 = create_course_topic(course=self.course, key="python-syntax", name="Python Syntax", order=1)
        self.topic2 = create_course_topic(course=self.course, key="pandas-dataframes", name="Pandas DataFrames", order=2)
        self.topic3 = create_course_topic(course=self.course, key="numpy-arrays", name="NumPy Arrays", order=3)

    def _create_mc_question(self, quiz, topic, text, correct_choice_idx=0):
        q = Question.objects.create(
            quiz=quiz,
            question_type="multiple_choice",
            text=text,
            explanation="Explanation",
        )
        link_question_to_topic(question=q, topic=topic)
        choices = []
        for i in range(3):
            c = Choice.objects.create(
                question=q,
                text=f"Option {i+1} for {text}",
                is_correct=(i == correct_choice_idx),
            )
            choices.append(c)
        return q, choices

    def test_question_selection_stratified_across_topics(self):
        """Questions are sampled across all course topics, bounded between 5 and 10."""
        # Create 3 questions for topic 1, 3 for topic 2, 3 for topic 3 = 9 total
        for i in range(3):
            self._create_mc_question(self.quiz1, self.topic1, f"Q1_{i+1}")
            self._create_mc_question(self.quiz2, self.topic2, f"Q2_{i+1}")
            self._create_mc_question(self.quiz2, self.topic3, f"Q3_{i+1}")

        selected = select_knowledge_check_questions(self.course)
        self.assertGreaterEqual(len(selected), 5)
        self.assertLessEqual(len(selected), 10)

        # Check topics represented
        represented_topics = set()
        for q in selected:
            for link in q.topic_links.all():
                represented_topics.add(link.topic_id)

        self.assertIn(self.topic1.id, represented_topics)
        self.assertIn(self.topic2.id, represented_topics)
        self.assertIn(self.topic3.id, represented_topics)

    def test_check_not_offered_when_insufficient_topic_questions(self):
        """Course with fewer than 3 topic questions does not offer the check."""
        # Only 2 questions created
        self._create_mc_question(self.quiz1, self.topic1, "Only Q1")
        self._create_mc_question(self.quiz2, self.topic2, "Only Q2")

        check = get_or_create_knowledge_check(self.user, self.course)
        self.assertIsNone(check)

    def test_check_offered_when_sufficient_topic_questions(self):
        """Course with >= 3 topic questions creates check in STATUS_OFFERED."""
        for i in range(3):
            self._create_mc_question(self.quiz1, self.topic1, f"Q_{i+1}")

        check = get_or_create_knowledge_check(self.user, self.course)
        self.assertIsNotNone(check)
        self.assertEqual(check.status, StartingKnowledgeCheck.STATUS_OFFERED)
        self.assertEqual(check.total_questions, len(check.question_ids))

    def test_dismissal_ensures_check_never_reappears(self):
        """Dismissing the check permanently marks it dismissed."""
        for i in range(4):
            self._create_mc_question(self.quiz1, self.topic1, f"Q_{i+1}")

        check = get_or_create_knowledge_check(self.user, self.course)
        self.assertEqual(check.status, StartingKnowledgeCheck.STATUS_OFFERED)

        # Learner dismisses
        response = self.client.post(reverse("courses:knowledge_check_dismiss", args=[self.course.id]))
        self.assertEqual(response.status_code, 302)

        check.refresh_from_db()
        self.assertEqual(check.status, StartingKnowledgeCheck.STATUS_DISMISSED)

        # Course detail view no longer shows the offer card
        response_detail = self.client.get(reverse("courses:course_detail", args=[self.course.id]))
        self.assertNotContains(response_detail, "✨ Starting Knowledge Check")

    def test_grading_zero_grade_impact_and_topic_evidence_calculation(self):
        """Grading records topic evidence without creating QuizAttempt records or modifying course grades."""
        # Topic 1: 2 questions
        q1, c1 = self._create_mc_question(self.quiz1, self.topic1, "Topic1 Q1", correct_choice_idx=0)
        q2, c2 = self._create_mc_question(self.quiz1, self.topic1, "Topic1 Q2", correct_choice_idx=0)

        # Topic 2: 2 questions
        q3, c3 = self._create_mc_question(self.quiz2, self.topic2, "Topic2 Q1", correct_choice_idx=0)
        q4, c4 = self._create_mc_question(self.quiz2, self.topic2, "Topic2 Q2", correct_choice_idx=0)

        check = get_or_create_knowledge_check(self.user, self.course)
        self.assertIsNotNone(check)

        # Answer Topic 1 correctly, Topic 2 incorrectly
        answers = {
            str(q1.id): {"choice_id": c1[0].id},  # correct
            str(q2.id): {"choice_id": c2[0].id},  # correct
            str(q3.id): {"choice_id": c3[1].id},  # incorrect
            str(q4.id): {"choice_id": c4[1].id},  # incorrect
        }

        results = grade_knowledge_check(check, answers)

        # Zero grade impact verification:
        self.assertEqual(QuizAttempt.objects.count(), 0)

        # Check status and score: 2 of 4 = 50%
        check.refresh_from_db()
        self.assertEqual(check.status, StartingKnowledgeCheck.STATUS_COMPLETED)
        self.assertEqual(check.score, 50.0)

        # Topic 1: 100% (Demonstrated)
        evidence_t1 = check.topic_evidence[str(self.topic1.id)]
        self.assertEqual(evidence_t1["accuracy"], 100.0)
        self.assertFalse(evidence_t1["needs_attention"])
        self.assertEqual(evidence_t1["status_label"], "Demonstrated")

        # Topic 2: 0% (Review Recommended)
        evidence_t2 = check.topic_evidence[str(self.topic2.id)]
        self.assertEqual(evidence_t2["accuracy"], 0.0)
        self.assertTrue(evidence_t2["needs_attention"])
        self.assertEqual(evidence_t2["status_label"], "Review Recommended")

        # Suggested topics: Topic 2 is recommended
        self.assertIn("pandas-dataframes", check.suggested_topic_keys)
        self.assertNotIn("python-syntax", check.suggested_topic_keys)

        # Study Focus has NOT been created/adopted yet
        self.assertFalse(check.focus_adopted)
        self.assertEqual(LearningFocus.objects.filter(user=self.user).count(), 0)

    def test_focus_adoption_requires_explicit_user_approval(self):
        """Study Focus is only created or updated after user explicitly approves the suggestion."""
        for i in range(4):
            self._create_mc_question(self.quiz1, self.topic1, f"Q_{i+1}")

        check = get_or_create_knowledge_check(self.user, self.course)
        grade_knowledge_check(check, {})

        # User explicitly approves topic1 and topic2
        focus = adopt_knowledge_check_focus(self.user, check, ["python-syntax", "pandas-dataframes"])
        self.assertIsNotNone(focus)
        self.assertEqual(focus.status, LearningFocus.STATUS_ACTIVE)
        self.assertEqual(focus.linked_course, self.course)
        self.assertIn("Python Syntax", focus.topic_names)
        self.assertIn("Pandas DataFrames", focus.topic_names)

        check.refresh_from_db()
        self.assertTrue(check.focus_adopted)

    def test_full_views_workflow(self):
        """End-to-end integration test of the Starting Knowledge Check flow."""
        q1, c1 = self._create_mc_question(self.quiz1, self.topic1, "Syntax Q1", correct_choice_idx=0)
        q2, c2 = self._create_mc_question(self.quiz1, self.topic1, "Syntax Q2", correct_choice_idx=0)
        q3, c3 = self._create_mc_question(self.quiz2, self.topic2, "DataFrames Q1", correct_choice_idx=0)
        q4, c4 = self._create_mc_question(self.quiz2, self.topic2, "DataFrames Q2", correct_choice_idx=0)

        # 1. Course detail offers the check
        response = self.client.get(reverse("courses:course_detail", args=[self.course.id]))
        self.assertContains(response, "✨ Starting Knowledge Check")
        self.assertContains(response, "Start Quick Check")
        self.assertContains(response, "Skip for Now")

        # 2. Start page displays the questions
        response_start = self.client.get(reverse("courses:knowledge_check_start", args=[self.course.id]))
        self.assertEqual(response_start.status_code, 200)
        self.assertContains(response_start, "Syntax Q1")
        self.assertContains(response_start, "Submit Knowledge Check")

        # 3. Submit answers
        response_submit = self.client.post(
            reverse("courses:knowledge_check_submit", args=[self.course.id]),
            data={
                f"question_{q1.id}": c1[0].id,
                f"question_{q2.id}": c2[0].id,
                f"question_{q3.id}": c3[1].id,
                f"question_{q4.id}": c4[1].id,
            },
        )
        self.assertEqual(response_submit.status_code, 302)
        self.assertRedirects(response_submit, reverse("courses:knowledge_check_results", args=[self.course.id]))

        # 4. Results page displays score and topic suggestions
        response_results = self.client.get(reverse("courses:knowledge_check_results", args=[self.course.id]))
        self.assertEqual(response_results.status_code, 200)
        self.assertContains(response_results, "50%")
        self.assertContains(response_results, "Approve Your Personalized Study Focus")
        self.assertContains(response_results, "pandas-dataframes")

        # 5. Adopt focus with approved topics
        response_adopt = self.client.post(
            reverse("courses:knowledge_check_adopt_focus", args=[self.course.id]),
            data={"topic_keys": ["pandas-dataframes"]},
        )
        self.assertEqual(response_adopt.status_code, 302)

        # Course detail now shows active focus, and knowledge check offer is gone
        response_final = self.client.get(reverse("courses:course_detail", args=[self.course.id]))
        self.assertNotContains(response_final, "✨ Starting Knowledge Check")
        self.assertContains(response_final, "Study Plan for Data Science 101")
        self.assertContains(response_final, "Pandas DataFrames")
