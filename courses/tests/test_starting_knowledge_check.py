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

import json
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    Course,
    Chapter,
    Quiz,
    Question,
    Choice,
    CourseTopic,
    StartingKnowledgeCheck,
    LearningFocus,
    QuizAttempt,
    UserProfile,
    ChapterCompletion,
)
from courses.topic_services import (
    create_course_topic,
    link_question_to_topic,
    get_course_topics_evidence_map,
)
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

    def test_te2_evidence_integration_and_supersedence(self):
        """Starting knowledge check feeds baseline topic evidence, which is superseded by quiz attempts."""
        q1, c1 = self._create_mc_question(self.quiz1, self.topic1, "Syntax Question 1", correct_choice_idx=0)
        q2, c2 = self._create_mc_question(self.quiz1, self.topic1, "Syntax Question 2", correct_choice_idx=0)
        q3, c3 = self._create_mc_question(self.quiz1, self.topic1, "Syntax Question 3", correct_choice_idx=0)

        check = StartingKnowledgeCheck.objects.create(
            user=self.user,
            course=self.course,
            status=StartingKnowledgeCheck.STATUS_OFFERED,
            question_ids=[q1.id, q2.id],
            total_questions=2,
        )
        # Learner answers q1 correctly, q2 incorrectly, q3 not in check
        grade_knowledge_check(
            check,
            {
                str(q1.id): {"choice_id": c1[0].id},  # correct
                str(q2.id): {"choice_id": c2[1].id},  # incorrect
            },
        )

        # 1. Inspect evidence map immediately after check
        evidence_map = get_course_topics_evidence_map(self.user, self.course)
        t1_ev = evidence_map[self.topic1.id]
        self.assertEqual(t1_ev["unique_questions_assessed"], 2)
        self.assertEqual(t1_ev["correct_count"], 1)
        self.assertEqual(t1_ev["incorrect_count"], 1)
        self.assertEqual(t1_ev["accuracy"], 50.0)
        self.assertEqual(t1_ev["source_counts"]["baseline"], 2)
        self.assertEqual(t1_ev["source_counts"]["quiz"], 0)
        self.assertEqual(t1_ev["evidence_sources"], ["baseline"])

        # 2. Learner takes chapter quiz later, answering q2 correctly (resolving mistake)
        attempt = QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz1,
            score=100.0,
            total_questions=1,
            review_data={
                "review_items": [
                    {
                        "question_id": q2.id,
                        "is_correct": True,
                        "earned_points": 1.0,
                        "maximum_points": 1.0,
                    }
                ]
            },
        )

        # Evidence map: q2 result is now superseded by quiz
        evidence_map2 = get_course_topics_evidence_map(self.user, self.course)
        t1_ev2 = evidence_map2[self.topic1.id]
        self.assertEqual(t1_ev2["unique_questions_assessed"], 2)
        self.assertEqual(t1_ev2["correct_count"], 2)  # both q1 (baseline) and q2 (quiz) are now correct
        self.assertEqual(t1_ev2["incorrect_count"], 0)
        self.assertEqual(t1_ev2["accuracy"], 100.0)
        self.assertEqual(t1_ev2["source_counts"]["baseline"], 2)
        self.assertEqual(t1_ev2["source_counts"]["quiz"], 1)
        self.assertIn("baseline", t1_ev2["evidence_sources"])
        self.assertIn("quiz", t1_ev2["evidence_sources"])

    def test_question_leakage_mitigation_on_first_quiz_attempt(self):
        """Questions seen in Starting Knowledge Check are excluded from learner's first Chapter Quiz attempt."""
        # Create 5 questions in quiz 1
        q_list = []
        for i in range(5):
            q, _ = self._create_mc_question(self.quiz1, self.topic1, f"Quiz1 Question {i+1}", correct_choice_idx=0)
            q_list.append(q)

        # Check samples q_list[0] and q_list[1]
        check = StartingKnowledgeCheck.objects.create(
            user=self.user,
            course=self.course,
            status=StartingKnowledgeCheck.STATUS_OFFERED,
            question_ids=[q_list[0].id, q_list[1].id],
            total_questions=2,
        )
        grade_knowledge_check(check, {})
        self.assertEqual(check.status, StartingKnowledgeCheck.STATUS_COMPLETED)

        # 1. Learner visits chapter quiz: should see 3 unseen questions (q_list[2], q_list[3], q_list[4])
        response = self.client.get(reverse("courses:chapter_quiz", args=[self.ch1.id]))
        self.assertEqual(response.status_code, 200)
        served_questions = response.context["quiz_questions"]
        self.assertEqual(len(served_questions), 3)
        served_ids = {q.id for q in served_questions}
        self.assertNotIn(q_list[0].id, served_ids)
        self.assertNotIn(q_list[1].id, served_ids)
        self.assertIn(q_list[2].id, served_ids)
        self.assertIn(q_list[3].id, served_ids)
        self.assertIn(q_list[4].id, served_ids)

        # 2. Submit quiz with answers for the 3 served questions
        payload = {
            "answers": {
                str(q_list[2].id): {"choice_id": q_list[2].choices.first().id},
                str(q_list[3].id): {"choice_id": q_list[3].choices.first().id},
                str(q_list[4].id): {"choice_id": q_list[4].choices.first().id},
            }
        }
        submit_res = self.client.post(
            reverse("courses:submit_quiz", args=[self.ch1.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(submit_res.status_code, 200)
        res_data = submit_res.json()
        self.assertEqual(res_data["percentage"], 100)
        self.assertEqual(QuizAttempt.objects.filter(user=self.user, quiz=self.quiz1).count(), 1)

        # 3. Retake visit: now that latest_attempt exists, all 5 questions are served
        response_retake = self.client.get(reverse("courses:chapter_quiz", args=[self.ch1.id]))
        self.assertEqual(response_retake.status_code, 200)
        retake_questions = response_retake.context["quiz_questions"]
        self.assertEqual(len(retake_questions), 5)

    def test_question_leakage_fallback_when_insufficient_unseen(self):
        """If fewer than 3 unseen questions remain, fallback to all questions to preserve quiz viability."""
        # Create 3 questions in quiz 1
        q_list = []
        for i in range(3):
            q, _ = self._create_mc_question(self.quiz1, self.topic1, f"Fallback Q {i+1}", correct_choice_idx=0)
            q_list.append(q)

        # Check used 2 of them, leaving only 1 unseen
        check = StartingKnowledgeCheck.objects.create(
            user=self.user,
            course=self.course,
            status=StartingKnowledgeCheck.STATUS_COMPLETED,
            question_ids=[q_list[0].id, q_list[1].id],
            total_questions=2,
        )

        response = self.client.get(reverse("courses:chapter_quiz", args=[self.ch1.id]))
        self.assertEqual(response.status_code, 200)
        served_questions = response.context["quiz_questions"]
        # Fallback to all 3 questions because 1 < 3
        self.assertEqual(len(served_questions), 3)

    def test_results_page_does_not_reveal_answer_keys_and_no_diagnostic_terms(self):
        """Results page only shows aggregate topic metrics, never reveals answer keys, and scrubs 'diagnostic'."""
        q1, c1 = self._create_mc_question(self.quiz1, self.topic1, "Secret Question Text", correct_choice_idx=0)
        q2, c2 = self._create_mc_question(self.quiz1, self.topic1, "Secret Question Text 2", correct_choice_idx=0)
        q3, c3 = self._create_mc_question(self.quiz2, self.topic2, "Secret Question Text 3", correct_choice_idx=0)

        check = get_or_create_knowledge_check(self.user, self.course)
        grade_knowledge_check(check, {str(q1.id): {"choice_id": c1[0].id}})

        response = self.client.get(reverse("courses:knowledge_check_results", args=[self.course.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Must not reveal question text or choice text
        self.assertNotIn("Secret Question Text", content)
        self.assertNotIn("Option 1 for", content)
        # Must not use the word 'diagnostic' in user-facing content
        self.assertNotIn("diagnostic", content.lower())

    def test_zero_academic_side_effects(self):
        """Starting Knowledge Check awards 0 XP, creates 0 QuizAttempts, and has zero academic side effects."""
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        initial_xp = profile.total_xp

        for i in range(3):
            self._create_mc_question(self.quiz1, self.topic1, f"SideEffect Q {i+1}")

        check = get_or_create_knowledge_check(self.user, self.course)
        grade_knowledge_check(check, {})

        profile.refresh_from_db()
        self.assertEqual(profile.total_xp, initial_xp)
        self.assertEqual(QuizAttempt.objects.filter(user=self.user).count(), 0)
        self.assertEqual(ChapterCompletion.objects.filter(user=self.user).count(), 0)
        self.assertEqual(LearningFocus.objects.filter(user=self.user).count(), 0)

    def test_state_transitions_and_single_offer_guarantee(self):
        """Checks follow strict Offered -> Abandon/Resume -> Completed/Dismissed transitions."""
        for i in range(3):
            self._create_mc_question(self.quiz1, self.topic1, f"Trans Q {i+1}")

        # 1. Initial course detail visit creates check and offers it
        res1 = self.client.get(reverse("courses:course_detail", args=[self.course.id]))
        self.assertContains(res1, "✨ Starting Knowledge Check")

        check = StartingKnowledgeCheck.objects.get(course=self.course, user=self.user)
        self.assertEqual(check.status, StartingKnowledgeCheck.STATUS_OFFERED)
        q_ids = list(check.question_ids)

        # 2. Abandoned check resumes without recreating or changing questions
        res2 = self.client.get(reverse("courses:knowledge_check_start", args=[self.course.id]))
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(StartingKnowledgeCheck.objects.filter(course=self.course).count(), 1)
        check.refresh_from_db()
        self.assertEqual(check.question_ids, q_ids)

        # 3. Dismissing marks as dismissed and removes banner permanently
        self.client.post(reverse("courses:knowledge_check_dismiss", args=[self.course.id]))
        check.refresh_from_db()
        self.assertEqual(check.status, StartingKnowledgeCheck.STATUS_DISMISSED)

        res3 = self.client.get(reverse("courses:course_detail", args=[self.course.id]))
        self.assertNotContains(res3, "✨ Starting Knowledge Check")
