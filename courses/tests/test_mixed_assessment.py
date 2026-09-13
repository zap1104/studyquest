import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from courses.models import Chapter, ChapterCompletion, Choice, Course, Question, Quiz, QuizAttempt, UserProfile
from courses.schemas import GenerationPreferences, get_assessment_mix
from courses.views import (
    _grade_question,
    calculate_quiz_xp,
    get_chapter_completion_state,
    get_chapter_status,
    normalize_text_answer,
)


class AssessmentMixSchemaTests(TestCase):
    def test_supported_mix_calculations_sum_to_ten(self):
        selections = [
            ["multiple_choice"],
            ["identification"],
            ["enumeration"],
            ["multiple_choice", "identification", "enumeration"],
        ]

        for selection in selections:
            self.assertEqual(sum(get_assessment_mix(selection).values()), 10)

    def test_empty_selection_falls_back_to_multiple_choice_mix(self):
        self.assertEqual(
            get_assessment_mix([]),
            {"multiple_choice": 7, "true_false": 3, "identification": 0, "enumeration": 0},
        )

    def test_preferences_deduplicate_formats(self):
        preferences = GenerationPreferences(
            assessment_formats=["multiple_choice", "identification", "multiple_choice"]
        )

        self.assertEqual(preferences.assessment_formats, ["multiple_choice", "identification"])


class QuestionGradingLogicTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="grading-student", password="password123")
        self.course = Course.objects.create(user=self.user, title="Networking")
        self.chapter = Chapter.objects.create(course=self.course, order=1, title="VLANs")
        self.quiz = Quiz.objects.create(chapter=self.chapter, title="VLAN Quiz")

    def test_text_normalization(self):
        self.assertEqual(normalize_text_answer("  IEEE 802.1Q  "), "ieee 802.1q")
        self.assertEqual(normalize_text_answer("Network-Segmentation"), "network segmentation")
        self.assertEqual(normalize_text_answer("Enhanced   Security..."), "enhanced security")

    def test_identification_accepts_variant_and_rejects_wrong_answer(self):
        question = Question.objects.create(
            quiz=self.quiz,
            order=1,
            question_type="identification",
            text="Name the tagging protocol.",
            answer_data={"accepted_answers": ["IEEE 802.1Q", "802.1Q", "Dot1q"]},
        )

        earned, maximum, feedback = _grade_question(question, {"text": "  802.1q  "})
        self.assertEqual((earned, maximum), (1, 1))
        self.assertTrue(feedback["is_correct"])

        earned, _, feedback = _grade_question(question, {"text": "ISL Protocol"})
        self.assertEqual(earned, 0)
        self.assertFalse(feedback["is_correct"])

    def test_enumeration_awards_partial_credit_and_rejects_duplicates(self):
        question = Question.objects.create(
            quiz=self.quiz,
            order=2,
            question_type="enumeration",
            text="Enumerate the benefits.",
            answer_data={
                "order_matters": False,
                "expected_items": [
                    {"canonical": "Segmentation", "accepted_variants": ["Network Segmentation"]},
                    {"canonical": "Security", "accepted_variants": ["Enhanced Security"]},
                    {"canonical": "Traffic Management", "accepted_variants": []},
                ],
            },
        )

        earned, maximum, feedback = _grade_question(
            question,
            {"items": ["network segmentation", "security", "security"]},
        )
        self.assertEqual((earned, maximum), (2, 3))
        self.assertEqual(set(feedback["matched_items"]), {"Segmentation", "Security"})
        self.assertEqual(feedback["missing_items"], ["Traffic Management"])

    def test_calculate_quiz_xp_tiers(self):
        self.assertEqual([calculate_quiz_xp(score) for score in (40, 60, 74, 75, 85, 100)], [10, 20, 20, 35, 45, 60])


class MixedAssessmentFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mixed-flow-user",
            password="temporary-test-password",
        )
        self.course = Course.objects.create(
            user=self.user,
            title="Mixed Assessment Course",
            description="Test course",
        )
        self.chapter = Chapter.objects.create(
            course=self.course,
            order=1,
            title="Assessment Fundamentals",
            review_content="Read this lesson before testing.",
        )
        self.quiz = Quiz.objects.create(chapter=self.chapter, title="Mixed Quiz")
        self.identification = Question.objects.create(
            quiz=self.quiz,
            order=1,
            question_type="identification",
            text="Identify the framework.",
            explanation="Zachman is the accepted framework.",
            answer_data={"accepted_answers": ["Zachman Framework", "Zachman"]},
        )
        self.enumeration = Question.objects.create(
            quiz=self.quiz,
            order=2,
            question_type="enumeration",
            text="List the domains.",
            explanation="These are the three domains.",
            answer_data={
                "expected_items": [
                    {"canonical": "Business", "accepted_variants": []},
                    {"canonical": "Data", "accepted_variants": ["Information"]},
                    {"canonical": "Technology", "accepted_variants": []},
                ],
                "order_matters": False,
            },
        )
        self.client.login(username="mixed-flow-user", password="temporary-test-password")

    def post_json(self, url, payload):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_initial_quiz_payload_does_not_expose_answer_keys(self):
        response = self.client.get(f"/chapters/{self.chapter.pk}/quiz/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"isCorrect", response.content)
        self.assertNotIn(b"Zachman Framework", response.content)
        self.assertNotIn(b"Business", response.content)

    def test_identification_and_enumeration_are_graded_server_side(self):
        identification_response = self.post_json(
            f"/chapters/{self.chapter.pk}/quiz/check/",
            {
                "question_id": self.identification.pk,
                "answer": {"text": " zachman "},
            },
        )
        enumeration_response = self.post_json(
            f"/chapters/{self.chapter.pk}/quiz/check/",
            {
                "question_id": self.enumeration.pk,
                "answer": {"items": ["Business", "Information"]},
            },
        )

        self.assertEqual(identification_response.json()["earned_points"], 1)
        self.assertEqual(enumeration_response.json()["earned_points"], 2)
        self.assertEqual(enumeration_response.json()["maximum_points"], 3)

    def test_malformed_answers_fail_as_incorrect_instead_of_server_errors(self):
        response = self.post_json(
            f"/chapters/{self.chapter.pk}/quiz/check/",
            {
                "question_id": self.identification.pk,
                "answer": {"text": {"unexpected": "object"}},
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["earned_points"], 0)

    def test_final_submission_uses_point_percentage(self):
        response = self.post_json(
            f"/chapters/{self.chapter.pk}/quiz/submit/",
            {
                "answers": {
                    str(self.identification.pk): {"text": "Zachman"},
                    str(self.enumeration.pk): {"items": ["Business", "Data", "Technology"]},
                },
            },
        )

        result = response.json()
        self.assertEqual(result["score"], 4)
        self.assertEqual(result["total_questions"], 4)
        self.assertEqual(result["percentage"], 100)
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["results"][0]["order"], 1)
        self.assertEqual(result["results"][0]["question_text"], "Identify the framework.")
        self.assertEqual(result["results"][0]["submitted_answer"], {"text": "Zachman"})
        self.assertEqual(result["results"][1]["matched_items"], ["Business", "Data", "Technology"])

    def test_quiz_xp_is_based_on_percentage_bands(self):
        self.assertEqual(calculate_quiz_xp(49), 10)
        self.assertEqual(calculate_quiz_xp(50), 20)
        self.assertEqual(calculate_quiz_xp(74), 20)
        self.assertEqual(calculate_quiz_xp(75), 35)
        self.assertEqual(calculate_quiz_xp(85), 45)
        self.assertEqual(calculate_quiz_xp(100), 60)


class QuizEndpointIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="endpoint-student", password="password123")
        self.course = Course.objects.create(user=self.user, title="Networking")
        self.chapter = Chapter.objects.create(course=self.course, order=1, title="VLANs")
        self.quiz = Quiz.objects.create(chapter=self.chapter, title="VLAN Quiz")
        self.choice_question = Question.objects.create(
            quiz=self.quiz,
            order=1,
            question_type="multiple_choice",
            text="What does this standard provide?",
            explanation="It is a trunk tagging protocol.",
        )
        self.correct_choice = Choice.objects.create(
            question=self.choice_question,
            text="Trunk tagging",
            is_correct=True,
        )
        Choice.objects.create(question=self.choice_question, text="Encryption")
        self.identification = Question.objects.create(
            quiz=self.quiz,
            order=2,
            question_type="identification",
            text="Name the protocol used on trunk links.",
            explanation="802.1Q is the answer.",
            answer_data={"accepted_answers": ["802.1Q", "IEEE 802.1Q"]},
        )
        self.client.login(username="endpoint-student", password="password123")

    def post_json(self, name, payload):
        return self.client.post(
            reverse(name, kwargs={"pk": self.chapter.pk}),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_payload_hides_answer_keys_and_accepted_answers(self):
        response = self.client.get(reverse("courses:chapter_quiz", kwargs={"pk": self.chapter.pk}))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        payload = content.split('<script id="quiz-data"', 1)[1].split("</script>", 1)[0]
        self.assertNotIn('"is_correct"', payload)
        self.assertNotIn("802.1Q", payload)

    def test_check_endpoint_does_not_create_attempt_or_award_xp(self):
        response = self.post_json(
            "courses:check_quiz_answer",
            {"question_id": self.choice_question.pk, "answer": {"choice_id": self.correct_choice.pk}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_correct"])
        self.assertEqual(QuizAttempt.objects.count(), 0)
        self.assertEqual(UserProfile.objects.get(user=self.user).total_xp, 0)

    def test_submit_regrades_authoritatively_and_returns_review_audit(self):
        response = self.post_json(
            "courses:submit_quiz",
            {
                "answers": {
                    str(self.choice_question.pk): {"choice_id": self.correct_choice.pk},
                    str(self.identification.pk): {"text": "802.1q"},
                }
            },
        )

        data = response.json()
        self.assertEqual((data["score"], data["total_questions"], data["percentage"]), (2, 2, 100))
        self.assertEqual(data["xp_earned"], 60)
        self.assertEqual(QuizAttempt.objects.count(), 1)
        self.assertEqual(UserProfile.objects.get(user=self.user).total_xp, 60)
        self.assertEqual(data["results"][0]["submitted_choice_text"], "Trunk tagging")
        self.assertEqual(data["results"][1]["submitted_answer"], {"text": "802.1q"})

    def test_check_multiple_choice_contract_includes_correct_choice_text_and_status(self):
        response = self.post_json(
            "courses:check_quiz_answer",
            {"question_id": self.choice_question.pk, "answer": {"choice_id": 99999}},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "incorrect")
        self.assertEqual(data["points_awarded"], 0)
        self.assertEqual(data["maximum_points"], 1)
        self.assertEqual(data["correct_choice_text"], "Trunk tagging")
        self.assertEqual(data["explanation"], "It is a trunk tagging protocol.")

    def test_check_true_false_contract_includes_correct_choice_text(self):
        tf_q = Question.objects.create(
            quiz=self.quiz,
            order=3,
            question_type="true_false",
            text="VLANs operate at Layer 2.",
            explanation="They divide broadcast domains at L2.",
        )
        c_true = Choice.objects.create(question=tf_q, text="True", is_correct=True)
        Choice.objects.create(question=tf_q, text="False", is_correct=False)

        response = self.post_json(
            "courses:check_quiz_answer",
            {"question_id": tf_q.pk, "answer": {"choice_id": c_true.pk}},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "correct")
        self.assertEqual(data["points_awarded"], 1)
        self.assertEqual(data["correct_choice_text"], "True")

    def test_check_identification_contract_and_variant_deduplication(self):
        response = self.post_json(
            "courses:check_quiz_answer",
            {"question_id": self.identification.pk, "answer": {"text": "802.1Q"}},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "correct")
        self.assertEqual(data["canonical_answer"], "802.1Q")
        self.assertIn("IEEE 802.1Q", data["accepted_answers"])

        # Verify filtering canonical answer leaves distinct variants
        canonical_norm = data["canonical_answer"].strip().lower()
        variants = [a for a in data["accepted_answers"] if a.strip().lower() != canonical_norm]
        self.assertEqual(variants, ["IEEE 802.1Q"])

    def test_check_enumeration_contract_includes_expected_and_missing_items(self):
        enum_q = Question.objects.create(
            quiz=self.quiz,
            order=4,
            question_type="enumeration",
            text="Name RACI roles.",
            explanation="RACI matrix roles.",
            answer_data={
                "order_matters": False,
                "expected_items": [
                    {"canonical": "Responsible", "accepted_variants": []},
                    {"canonical": "Accountable", "accepted_variants": []},
                    {"canonical": "Consulted", "accepted_variants": []},
                    {"canonical": "Informed", "accepted_variants": []},
                ],
            },
        )

        # Partial answer
        response = self.post_json(
            "courses:check_quiz_answer",
            {"question_id": enum_q.pk, "answer": {"items": ["Responsible", "Accountable", "Consulted"]}},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "partial")
        self.assertEqual(data["points_awarded"], 3)
        self.assertEqual(data["maximum_points"], 4)
        self.assertEqual(data["missing_items"], ["Informed"])
        self.assertEqual(data["expected_items"], ["Responsible", "Accountable", "Consulted", "Informed"])

        # Correct answer
        response = self.post_json(
            "courses:check_quiz_answer",
            {"question_id": enum_q.pk, "answer": {"items": ["Responsible", "Accountable", "Consulted", "Informed"]}},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "correct")
        self.assertEqual(data["points_awarded"], 4)
        self.assertEqual(data["missing_items"], [])
        self.assertEqual(data["expected_items"], ["Responsible", "Accountable", "Consulted", "Informed"])


class ProgressionGateIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="progress-student", password="password123")
        self.course = Course.objects.create(user=self.user, title="Networking")
        self.chapter_one = Chapter.objects.create(course=self.course, order=1, title="Chapter 1")
        self.chapter_two = Chapter.objects.create(course=self.course, order=2, title="Chapter 2")
        self.quiz = Quiz.objects.create(chapter=self.chapter_one, title="Quiz 1")

    def test_future_chapter_starts_locked(self):
        self.assertEqual(get_chapter_status(self.user, self.chapter_one), "available")
        self.assertEqual(get_chapter_status(self.user, self.chapter_two), "locked")

    def test_quiz_pass_at_seventy_five_percent_with_reading_unlocks_chapter(self):
        ChapterCompletion.objects.create(user=self.user, chapter=self.chapter_one)
        QuizAttempt.objects.create(user=self.user, quiz=self.quiz, score=3, total_questions=4, xp_earned=35)

        state = get_chapter_completion_state(self.user, self.chapter_one)
        self.assertTrue(state["completed"])
        self.assertEqual(state["unlock_route"], "guided")
        self.assertFalse(state["tested_out"])
        self.assertEqual(get_chapter_status(self.user, self.chapter_one), "completed")
        self.assertEqual(get_chapter_status(self.user, self.chapter_two), "available")

    def test_quiz_pass_at_seventy_five_percent_without_reading_does_not_unlock(self):
        # 3/4 = 75%, passes the quiz itself but does not bypass without reading
        QuizAttempt.objects.create(user=self.user, quiz=self.quiz, score=3, total_questions=4, xp_earned=35)

        state = get_chapter_completion_state(self.user, self.chapter_one)
        self.assertTrue(state["quiz_passed"])
        self.assertFalse(state["completed"])
        self.assertIsNone(state["unlock_route"])
        self.assertFalse(state["tested_out"])
        self.assertEqual(get_chapter_status(self.user, self.chapter_one), "available")
        self.assertEqual(get_chapter_status(self.user, self.chapter_two), "locked")

    def test_quiz_pass_at_ninety_percent_without_reading_unlocks_via_prior_knowledge(self):
        # 4/4 = 100% (>= 90%), tests out without reading
        QuizAttempt.objects.create(user=self.user, quiz=self.quiz, score=4, total_questions=4, xp_earned=60)

        state = get_chapter_completion_state(self.user, self.chapter_one)
        self.assertTrue(state["completed"])
        self.assertEqual(state["unlock_route"], "prior_knowledge")
        self.assertTrue(state["tested_out"])
        self.assertEqual(get_chapter_status(self.user, self.chapter_one), "completed")
        self.assertEqual(get_chapter_status(self.user, self.chapter_two), "available")

    def test_below_seventy_five_percent_without_reading_does_not_unlock(self):
        QuizAttempt.objects.create(user=self.user, quiz=self.quiz, score=2, total_questions=4, xp_earned=20)

        state = get_chapter_completion_state(self.user, self.chapter_one)
        self.assertFalse(state["quiz_passed"])
        self.assertFalse(state["completed"])
        self.assertEqual(get_chapter_status(self.user, self.chapter_one), "available")
        self.assertEqual(get_chapter_status(self.user, self.chapter_two), "locked")
