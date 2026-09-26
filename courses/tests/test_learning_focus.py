from django.test import TestCase
from django.contrib.auth.models import User
from courses.models import Course, LearningFocus
from courses.learning_focus_service import (
    validate_external_assessment_payload,
    import_external_assessment,
    create_manual_learning_focus,
    activate_learning_focus,
    dismiss_learning_focus,
    complete_learning_focus,
    link_focus_to_course,
    get_active_learning_focus,
    get_pending_learning_focus,
    find_matching_courses,
    generate_course_creation_prefill,
    DiagnosticPayloadError,
)


class LearningFocusServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="student1", password="password123")
        self.other_user = User.objects.create_user(username="student2", password="password123")

        self.sample_payload = {
            "schema_version": "1.0",
            "external_assessment_id": "assessment-2026-00182",
            "student_reference": "23-22-040",
            "assessment_title": "BSIT Learning Gap Assessment",
            "assessed_at": "2026-09-27T08:00:00+08:00",
            "subject_results": [
                {
                    "subject_code": "SIA",
                    "subject_name": "Systems Integration and Architecture",
                    "score": 58,
                    "maximum_score": 100,
                    "priority": "high",
                    "weak_topics": [
                        {"topic": "Enterprise Architecture Governance", "score": 45},
                        {"topic": "Tool Sprawl", "score": 50},
                        {"topic": "Universal Project Lifecycle", "score": 60},
                    ],
                }
            ],
            "recommended_focus": {
                "subject_code": "SIA",
                "topics": [
                    "Enterprise Architecture Governance",
                    "Tool Sprawl",
                ],
            },
        }

    def test_validate_valid_payload(self):
        is_valid, err, normalized = validate_external_assessment_payload(self.sample_payload)
        self.assertTrue(is_valid)
        self.assertEqual(err, "")
        self.assertEqual(normalized["external_assessment_id"], "assessment-2026-00182")
        self.assertEqual(normalized["subject_code"], "SIA")
        self.assertEqual(normalized["subject_name"], "Systems Integration and Architecture")
        self.assertEqual(len(normalized["topic_names"]), 2)
        self.assertIn("Tool Sprawl", normalized["topic_names"])
        self.assertEqual(normalized["initial_score"], 58.0)
        self.assertIsNotNone(normalized["assessed_at"])

    def test_validate_missing_assessment_id(self):
        bad_payload = dict(self.sample_payload)
        bad_payload["external_assessment_id"] = "  "
        is_valid, err, _ = validate_external_assessment_payload(bad_payload)
        self.assertFalse(is_valid)
        self.assertIn("external_assessment_id", err)

    def test_validate_missing_subject(self):
        bad_payload = {
            "schema_version": "1.0",
            "external_assessment_id": "assess-01",
        }
        is_valid, err, _ = validate_external_assessment_payload(bad_payload)
        self.assertFalse(is_valid)
        self.assertIn("subject", err)

    def test_import_external_assessment_success(self):
        focus, created, msg = import_external_assessment(self.user, self.sample_payload)
        self.assertTrue(created)
        self.assertEqual(focus.user, self.user)
        self.assertEqual(focus.source, LearningFocus.SOURCE_EXTERNAL)
        self.assertEqual(focus.status, LearningFocus.STATUS_PENDING)
        self.assertEqual(focus.subject_code, "SIA")
        self.assertEqual(focus.subject_name, "Systems Integration and Architecture")
        self.assertEqual(len(focus.topic_names), 2)
        self.assertEqual(focus.initial_score, 58.0)

    def test_import_external_assessment_idempotency(self):
        focus1, created1, _ = import_external_assessment(self.user, self.sample_payload)
        self.assertTrue(created1)

        # Attempt to import identical assessment again
        focus2, created2, msg = import_external_assessment(self.user, self.sample_payload)
        self.assertFalse(created2)
        self.assertEqual(focus1.id, focus2.id)
        self.assertIn("already", msg.lower())

    def test_import_invalid_payload_raises(self):
        with self.assertRaises(DiagnosticPayloadError):
            import_external_assessment(self.user, {"invalid": "payload"})

    def test_create_manual_learning_focus(self):
        focus = create_manual_learning_focus(
            user=self.user,
            subject_name="Cloud Computing",
            topic_names=["Serverless Architecture", "IAM Policies"],
            subject_code="CC101",
            reason="Low midterm exam score",
        )
        self.assertEqual(focus.user, self.user)
        self.assertEqual(focus.source, LearningFocus.SOURCE_MANUAL)
        self.assertEqual(focus.status, LearningFocus.STATUS_ACTIVE)
        self.assertEqual(focus.subject_name, "Cloud Computing")
        self.assertEqual(len(focus.topic_names), 2)
        self.assertEqual(focus.source_payload.get("reason"), "Low midterm exam score")

    def test_activate_focus_completes_previous_active_focus(self):
        focus1 = create_manual_learning_focus(self.user, "Subject 1", activate=True)
        self.assertEqual(focus1.status, LearningFocus.STATUS_ACTIVE)

        focus2, _, _ = import_external_assessment(self.user, self.sample_payload)
        self.assertEqual(focus2.status, LearningFocus.STATUS_PENDING)

        activate_learning_focus(focus2)
        focus1.refresh_from_db()
        focus2.refresh_from_db()

        self.assertEqual(focus1.status, LearningFocus.STATUS_COMPLETED)
        self.assertEqual(focus2.status, LearningFocus.STATUS_ACTIVE)

    def test_dismiss_and_complete_focus(self):
        focus = create_manual_learning_focus(self.user, "Data Structures")
        self.assertEqual(focus.status, LearningFocus.STATUS_ACTIVE)

        dismiss_learning_focus(focus)
        focus.refresh_from_db()
        self.assertEqual(focus.status, LearningFocus.STATUS_DISMISSED)

        complete_learning_focus(focus)
        focus.refresh_from_db()
        self.assertEqual(focus.status, LearningFocus.STATUS_COMPLETED)

    def test_link_focus_to_course_ownership_enforced(self):
        course = Course.objects.create(user=self.user, title="SIA Course")
        other_course = Course.objects.create(user=self.other_user, title="Other Course")

        focus = create_manual_learning_focus(self.user, "Systems Integration")

        # Success linking own course
        link_focus_to_course(focus, course)
        focus.refresh_from_db()
        self.assertEqual(focus.linked_course, course)

        # Fails linking other user's course
        with self.assertRaises(ValueError):
            link_focus_to_course(focus, other_course)

    def test_find_matching_courses(self):
        c1 = Course.objects.create(user=self.user, title="Systems Integration and Architecture (SIA)")
        c2 = Course.objects.create(user=self.user, title="Database Management Systems")
        c_other = Course.objects.create(user=self.other_user, title="SIA Other Student")

        focus = create_manual_learning_focus(self.user, "Systems Integration and Architecture", subject_code="SIA")
        matches = find_matching_courses(self.user, focus)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0], c1)

    def test_generate_course_creation_prefill(self):
        focus = create_manual_learning_focus(
            self.user,
            "Operating Systems",
            topic_names=["Deadlocks", "Memory Paging"],
        )
        focus.initial_score = 48.0
        focus.save()

        prefill = generate_course_creation_prefill(focus)
        self.assertEqual(prefill["title"], "Operating Systems Focus Review")
        self.assertIn("Deadlocks, Memory Paging", prefill["review_emphasis"])
        self.assertIn("48%", prefill["review_emphasis"])
        self.assertEqual(prefill["focus_id"], focus.id)
