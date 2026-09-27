from django.test import TestCase
from django.contrib.auth.models import User
from courses.models import Course, Chapter, Quiz, QuizAttempt, LearningFocus, Question
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
    annotate_course_chapters_with_focus,
    calculate_focus_mastery,
    get_academic_catalogue,
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


class LearningFocusViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="student1", password="password123")
        self.client.login(username="student1", password="password123")
        self.sample_payload = {
            "schema_version": "1.0",
            "external_assessment_id": "assessment-view-001",
            "student_reference": "23-22-040",
            "assessment_title": "BSIT Diagnostic Assessment",
            "subject_results": [
                {
                    "subject_code": "SIA",
                    "subject_name": "Systems Integration and Architecture",
                    "score": 55,
                    "weak_topics": [
                        {"topic": "Tool Sprawl"},
                        {"topic": "Architecture Governance"},
                    ],
                }
            ],
            "recommended_focus": {
                "subject_code": "SIA",
                "topics": ["Tool Sprawl", "Architecture Governance"],
            },
        }

    def test_dashboard_displays_active_focus(self):
        create_manual_learning_focus(
            self.user,
            subject_name="Enterprise Systems",
            topic_names=["SOA", "Microservices"],
            subject_code="ES101",
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enterprise Systems")
        self.assertContains(response, "SOA")
        self.assertContains(response, "Study Focus")
        self.assertIn("Today's Study Focus", response.content.decode())

    def test_dashboard_displays_active_focus_with_courses(self):
        from courses.models import Course, Chapter
        course = Course.objects.create(
            user=self.user,
            title="Software Architecture",
        )
        Chapter.objects.create(
            course=course,
            title="Intro to Arch",
            order=1,
            review_content="Content",
        )
        create_manual_learning_focus(
            self.user,
            subject_name="Cloud Computing",
            topic_names=["Serverless", "Containers"],
            subject_code="CS402",
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cloud Computing")
        self.assertContains(response, "Serverless")
        self.assertIn("Today's Study Focus", response.content.decode())

    def test_focus_checkin_json_post(self):
        import json
        response = self.client.post("/focus/checkin/", {
            "assessment_json": json.dumps(self.sample_payload)
        })
        self.assertEqual(response.status_code, 302)
        focus = LearningFocus.objects.filter(user=self.user, external_assessment_id="assessment-view-001").first()
        self.assertIsNotNone(focus)
        self.assertEqual(focus.status, LearningFocus.STATUS_PENDING)
        self.assertIn(f"/focus/{focus.id}/recommendation/", response.url)

    def test_focus_checkin_manual_post(self):
        response = self.client.post("/focus/checkin/", {
            "subject_name": "Network Security",
            "topic_names": "Firewalls, Encryption",
            "subject_code": "NETSEC",
            "reason": "Upcoming examination",
        })
        self.assertEqual(response.status_code, 302)
        focus = LearningFocus.objects.filter(user=self.user, subject_name="Network Security").first()
        self.assertIsNotNone(focus)
        self.assertEqual(focus.status, LearningFocus.STATUS_ACTIVE)
        self.assertEqual(len(focus.topic_names), 2)

    def test_focus_activate_view(self):
        focus, _, _ = import_external_assessment(self.user, self.sample_payload)
        self.assertEqual(focus.status, LearningFocus.STATUS_PENDING)

        response = self.client.post(f"/focus/{focus.id}/activate/")
        self.assertEqual(response.status_code, 302)
        focus.refresh_from_db()
        self.assertEqual(focus.status, LearningFocus.STATUS_ACTIVE)

    def test_focus_dismiss_view(self):
        focus = create_manual_learning_focus(self.user, "Software Engineering")
        self.assertEqual(focus.status, LearningFocus.STATUS_ACTIVE)

        response = self.client.post(f"/focus/{focus.id}/dismiss/")
        self.assertEqual(response.status_code, 302)
        focus.refresh_from_db()
        self.assertEqual(focus.status, LearningFocus.STATUS_DISMISSED)

    def test_focus_link_course_view(self):
        focus = create_manual_learning_focus(self.user, "Information Security")
        course = Course.objects.create(user=self.user, title="InfoSec Course")

        response = self.client.post(f"/focus/{focus.id}/link-course/{course.id}/")
        self.assertEqual(response.status_code, 302)
        focus.refresh_from_db()
        self.assertEqual(focus.linked_course, course)

    def test_course_create_prefill_with_focus_id(self):
        focus = create_manual_learning_focus(
            self.user,
            "Systems Architecture",
            topic_names=["Cloud Migration", "Kubernetes"],
        )
        response = self.client.get(f"/courses/new/?focus_id={focus.id}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Focus Mode Active")
        self.assertContains(response, "Systems Architecture Focus Review")
        self.assertContains(response, "Cloud Migration, Kubernetes")

    def test_dashboard_zero_courses_onboarding_visual_hierarchy(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Find My Study Focus")
        self.assertContains(response, "What would you like to improve?")
        self.assertContains(response, "Create a Course")
        self.assertContains(response, "Skip for now")
        self.assertNotContains(response, "Explore StudyQuest")
        self.assertNotContains(response, "Import Diagnostic Assessment")
        # Modal components for new user with zero courses:
        self.assertContains(response, "STUDY FOCUS")
        self.assertContains(response, "What would you like to focus on?")
        self.assertContains(response, "Choose a Subject or Topic")
        self.assertContains(response, "Use a School Result")
        self.assertContains(response, "Start With My Materials")
        self.assertNotContains(response, "Select From My Courses")

    def test_dashboard_returning_user_with_courses_shows_select_from_my_courses(self):
        Course.objects.create(user=self.user, title="DevOps Fundamentals")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select From My Courses")
        self.assertNotContains(response, "Start With My Materials")

    def test_annotate_course_chapters_with_focus(self):
        course = Course.objects.create(user=self.user, title="DevOps Fundamentals")
        ch1 = Chapter.objects.create(course=course, order=1, title="Intro to DevOps", review_content="Basics of CI/CD")
        ch2 = Chapter.objects.create(course=course, order=2, title="Containers & Orchestration", review_content="Deep dive into Kubernetes")
        ch3 = Chapter.objects.create(course=course, order=3, title="Monitoring", review_content="Metrics and observability")

        focus = create_manual_learning_focus(
            self.user,
            subject_name="DevOps",
            topic_names=["Kubernetes", "CI/CD"],
        )
        chapters = [ch1, ch2, ch3]
        annotate_course_chapters_with_focus(chapters, focus)

        self.assertTrue(ch1.is_focus_target)
        self.assertIn("CI/CD", ch1.matched_focus_topics)
        self.assertTrue(ch2.is_focus_target)
        self.assertIn("Kubernetes", ch2.matched_focus_topics)
        self.assertFalse(ch3.is_focus_target)

    def test_calculate_focus_mastery(self):
        course = Course.objects.create(user=self.user, title="Cloud Systems")
        ch1 = Chapter.objects.create(course=course, order=1, title="Serverless Architecture", review_content="Lambda & FaaS")
        quiz1 = Quiz.objects.create(chapter=ch1, title="Serverless Quiz")

        focus = create_manual_learning_focus(
            self.user,
            subject_name="Cloud Systems",
            topic_names=["Serverless"],
        )
        focus.initial_score = 40.0
        focus.save()
        link_focus_to_course(focus, course)

        # Before any quiz attempt
        mastery = calculate_focus_mastery(focus)
        self.assertFalse(mastery["is_mastered"])
        self.assertEqual(mastery["mastered_chapters"], 0)
        self.assertEqual(mastery["total_targets"], 1)

        # Record a passing quiz attempt (90%)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=quiz1,
            score=9.0,
            total_questions=10,
            xp_earned=25,
            review_data={"percentage": 90, "passed": True},
        )

        mastery_after = calculate_focus_mastery(focus)
        self.assertTrue(mastery_after["is_mastered"])
        self.assertEqual(mastery_after["mastered_chapters"], 1)
        self.assertEqual(mastery_after["average_score"], 90.0)
        self.assertEqual(mastery_after["gain"], 50.0)

    def test_course_detail_with_focus(self):
        course = Course.objects.create(user=self.user, title="Enterprise Systems")
        ch1 = Chapter.objects.create(course=course, order=1, title="Service Oriented Architecture", review_content="SOA overview")
        focus = create_manual_learning_focus(
            self.user,
            subject_name="Enterprise Systems",
            topic_names=["Service Oriented Architecture"],
        )
        link_focus_to_course(focus, course)

        response = self.client.get(f"/courses/{course.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Study Focus")
        self.assertContains(response, "Study Plan for Enterprise Systems")
        self.assertContains(response, "Focus Target")

    def test_chapter_review_with_focus_topics(self):
        course = Course.objects.create(user=self.user, title="Database Engineering")
        ch1 = Chapter.objects.create(course=course, order=1, title="Indexing and B-Trees", review_content="Optimizing B-Tree lookups")
        focus = create_manual_learning_focus(
            self.user,
            subject_name="Database Engineering",
            topic_names=["Indexing"],
        )
        link_focus_to_course(focus, course)

        response = self.client.get(f"/chapters/{ch1.pk}/review/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Focus Target:")
        self.assertContains(response, "Indexing")

    def test_focus_summary_view(self):
        course = Course.objects.create(user=self.user, title="Cybersecurity Operations")
        ch1 = Chapter.objects.create(course=course, order=1, title="Incident Response", review_content="SIEM alerts")
        quiz = Quiz.objects.create(chapter=ch1, title="IR Quiz")
        QuizAttempt.objects.create(
            user=self.user,
            quiz=quiz,
            score=8.5,
            total_questions=10,
            xp_earned=25,
            review_data={"percentage": 85, "passed": True},
        )

        focus = create_manual_learning_focus(
            self.user,
            subject_name="Cybersecurity Operations",
            topic_names=["Incident Response"],
        )
        focus.initial_score = 45.0
        focus.save()
        link_focus_to_course(focus, course)

        response = self.client.get(f"/focus/{focus.id}/summary/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Institutional Diagnostic Report")
        self.assertContains(response, "Cybersecurity Operations")
        self.assertContains(response, "Diagnostic Gap Remediated")
        self.assertContains(response, "45%")
        self.assertContains(response, "85%")
        self.assertContains(response, "+40%")

    def test_dungeon_launch_catalog_focus_annotation(self):
        from dungeon import services as dungeon_services
        from courses.models import Question

        course = Course.objects.create(user=self.user, title="Data Structures")
        ch1 = Chapter.objects.create(course=course, order=1, title="Binary Search Trees", review_content="BST traversals")
        quiz = Quiz.objects.create(chapter=ch1, title="BST Quiz")
        for i in range(5):
            Question.objects.create(
                quiz=quiz,
                text=f"Question {i+1} on BST",
                question_type="multiple_choice",
                order=i+1,
            )

        focus = create_manual_learning_focus(
            self.user,
            subject_name="Data Structures",
            topic_names=["Binary Search Trees"],
        )
        link_focus_to_course(focus, course)

        catalog = dungeon_services.build_launch_catalog(self.user, active_focus=focus)
        self.assertTrue(len(catalog) > 0)
        target_group = next((g for g in catalog if g["course"].id == course.id), None)
        self.assertIsNotNone(target_group)
        self.assertTrue(target_group["is_focus_course"])
        self.assertTrue(target_group["entries"][0]["is_focus_target"])

        # Launch page renders focus tags
        response = self.client.get("/dungeon/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remediation Expeditions for Data Structures")
        self.assertContains(response, "Focus Course")
        self.assertContains(response, "Focus Target")

    def test_df3_course_connection_screen(self):
        course = Course.objects.create(user=self.user, title="SIA Week 8-9")
        focus = create_manual_learning_focus(self.user, subject_name="SIA", topic_names=["Tool Sprawl"])
        response = self.client.get(f"/focus/{focus.id}/recommendation/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connect Your Study Focus")
        self.assertContains(response, "SIA Week 8-9")
        self.assertContains(response, "Use This Course")
        self.assertContains(response, "Upload Materials for a New Focused Course")

    def test_df4_check_your_understanding_card_on_course_detail(self):
        course = Course.objects.create(user=self.user, title="Distributed Systems")
        ch1 = Chapter.objects.create(course=course, order=1, title="Consensus Algorithms", review_content="Paxos & Raft")
        quiz = Quiz.objects.create(chapter=ch1, title="Consensus Quiz")
        for i in range(4):
            Question.objects.create(quiz=quiz, text=f"Q{i+1}", question_type="multiple_choice", order=i+1)

        focus = create_manual_learning_focus(self.user, subject_name="Distributed Systems", topic_names=["Consensus"])
        link_focus_to_course(focus, course)

        response = self.client.get(f"/courses/{course.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check Your Understanding")
        self.assertContains(response, "Start Check")
        self.assertContains(response, "Review Materials First")

    def test_df5_remediation_recommendations(self):
        from courses.learning_focus_service import get_focus_remediation_recommendations
        course = Course.objects.create(user=self.user, title="Algorithms")
        ch1 = Chapter.objects.create(course=course, order=1, title="Graph Traversal", review_content="BFS and DFS")
        quiz = Quiz.objects.create(chapter=ch1, title="Graph Quiz")

        focus = create_manual_learning_focus(self.user, subject_name="Algorithms", topic_names=["Graph"])
        link_focus_to_course(focus, course)

        # 1. Unpassed chapter recommendation
        rec = get_focus_remediation_recommendations(self.user, focus)
        self.assertTrue(rec["has_recommendation"])
        self.assertEqual(rec["action_type"], "chapter_review")

        # 2. After passing with 90%
        QuizAttempt.objects.create(user=self.user, quiz=quiz, score=9.0, total_questions=10, review_data={"percentage": 90, "passed": True})
        rec = get_focus_remediation_recommendations(self.user, focus)
        self.assertTrue(rec["has_recommendation"])
        self.assertEqual(rec["action_type"], "dungeon_quest")

    def test_df6_focus_progress_and_completion_toggle(self):
        course = Course.objects.create(user=self.user, title="Operating Systems")
        ch1 = Chapter.objects.create(course=course, order=1, title="Virtual Memory", review_content="Paging and TLB")
        quiz = Quiz.objects.create(chapter=ch1, title="VM Quiz")
        QuizAttempt.objects.create(user=self.user, quiz=quiz, score=8.5, total_questions=10, review_data={"percentage": 85, "passed": True})

        focus = create_manual_learning_focus(self.user, subject_name="Operating Systems", topic_names=["Virtual Memory"])
        focus.initial_score = 50.0
        focus.save()
        link_focus_to_course(focus, course)

        response = self.client.get(f"/focus/{focus.id}/summary/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Academic Non-Equivalence Notice")
        self.assertContains(response, "Topics Progress Breakdown")
        self.assertContains(response, "Virtual Memory")
        self.assertContains(response, "Mark Focus Complete")

        # Toggle complete
        post_resp = self.client.post(f"/focus/{focus.id}/complete/")
        self.assertEqual(post_resp.status_code, 302)
        focus.refresh_from_db()
        self.assertEqual(focus.status, LearningFocus.STATUS_COMPLETED)

        # Toggle reopen
        post_resp = self.client.post(f"/focus/{focus.id}/reopen/")
        self.assertEqual(post_resp.status_code, 302)
        focus.refresh_from_db()
        self.assertEqual(focus.status, LearningFocus.STATUS_ACTIVE)


class AcademicCatalogueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="student_catalog", password="password123")

    def test_academic_catalogue_service_structure(self):
        catalogue = get_academic_catalogue()
        self.assertIsInstance(catalogue, dict)
        self.assertEqual(catalogue.get("catalog_version"), "2026.1")
        areas = catalogue.get("areas", [])
        self.assertEqual(len(areas), 10)

        area_keys = {a["key"] for a in areas}
        expected_keys = {
            "mathematics",
            "natural_sciences",
            "computing",
            "business",
            "social_sciences",
            "humanities",
            "education",
            "engineering",
            "health_sciences",
            "academic_skills",
        }
        self.assertEqual(area_keys, expected_keys)

        # Verify key Philippine college subjects exist
        all_subjects = []
        for area in areas:
            all_subjects.extend(area.get("subjects", []))

        subject_labels = {s["label"] for s in all_subjects}
        self.assertIn("Calculus", subject_labels)
        self.assertIn("Technopreneurship", subject_labels)
        self.assertIn("Systems Integration and Architecture", subject_labels)
        self.assertIn("Readings in Philippine History", subject_labels)
        self.assertIn("Science, Technology and Society", subject_labels)

        # Verify Technopreneurship topics
        techno = next(s for s in all_subjects if s["label"] == "Technopreneurship")
        self.assertEqual(techno["code"], "TECHNO")
        self.assertIn("Tech Entrepreneurship", techno.get("aliases", []))
        topic_labels = [t["label"] for t in techno.get("topics", [])]
        self.assertIn("Business Model Canvas", topic_labels)
        self.assertIn("Intellectual Property", topic_labels)

        bmc_topic = next(t for t in techno.get("topics", []) if t["label"] == "Business Model Canvas")
        self.assertIn("BMC", bmc_topic.get("aliases", []))

    def test_academic_catalogue_api_endpoint(self):
        # 1. Unauthenticated -> 302 to login
        resp = self.client.get("/focus/catalogue/")
        self.assertEqual(resp.status_code, 302)

        # 2. Authenticated -> 200 JSON
        self.client.login(username="student_catalog", password="password123")
        resp = self.client.get("/focus/catalogue/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")

        data = resp.json()
        self.assertEqual(data.get("catalog_version"), "2026.1")
        self.assertEqual(len(data.get("areas", [])), 10)

    def test_dashboard_renders_academic_catalogue_context_and_markup(self):
        self.client.login(username="student_catalog", password="password123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

        # Catalogue embedded JSON and the drill-down root hooks
        self.assertContains(resp, 'id="academic-catalogue-data"')
        self.assertContains(resp, "Browse Subjects and Topics")
        self.assertContains(resp, 'id="view-step-catalogue"')
        self.assertContains(resp, 'data-catalogue-body')
        self.assertContains(resp, 'data-catalogue-list')
        self.assertContains(resp, 'data-catalogue-search')
        self.assertContains(resp, 'data-catalogue-tray-status')
        self.assertContains(resp, 'data-catalogue-cap-message')
        self.assertContains(resp, "2026.1")

    def test_dashboard_omits_old_nested_catalogue_layout(self):
        """The two-column explorer and its independent scroll panes are gone.

        These hooks are asserted absent so the nested-scroll regression cannot
        silently return.
        """
        self.client.login(username="student_catalog", password="password123")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

        for obsolete in (
            "catalogue-explorer-grid",
            "catalogue-areas-sidebar",
            "catalogue-subjects-panel",
            "catalogue-selection-tray",
            'id="catalogue-areas-list"',
            'id="catalogue-subjects-container"',
        ):
            self.assertNotContains(resp, obsolete)

    def test_catalogue_script_is_served_from_a_static_module(self):
        """The controller must live outside the template, not inline."""
        self.client.login(username="student_catalog", password="password123")
        resp = self.client.get("/")

        self.assertContains(resp, "courses/js/focus_catalogue.js")
        # The old inline controller's private state must not exist anywhere.
        for removed in (
            "catalogueSelectedTopics",
            "expandedSubjectKeys",
            "renderSubjectsPanel",
            "handleTopicToggle",
        ):
            self.assertNotContains(resp, removed)

    def test_catalogue_styles_moved_to_shared_stylesheet(self):
        """Catalogue and focus modal CSS must live in static stylesheet, not inline."""
        self.client.login(username="student_catalog", password="password123")
        resp = self.client.get("/")

        # Modal shell and catalogue hooks are present in markup
        self.assertContains(resp, "focus-catalogue-dialog")
        self.assertContains(resp, 'data-current-step="choice"')
        self.assertContains(resp, "data-catalogue-body")

        # No inline <style> block remains in the modal markup
        html = resp.content.decode()
        modal_start = html.find('id="focus-modal"')
        self.assertNotEqual(modal_start, -1)
        catalogue_data_pos = html.find('id="academic-catalogue-data"')
        modal_content = html[modal_start:catalogue_data_pos]
        self.assertNotIn("<style", modal_content)


class CatalogueSelectionSubmissionTests(TestCase):
    """The POST contract is unchanged: labels only, comma-joined."""

    def setUp(self):
        self.user = User.objects.create_user(username="student_submit", password="password123")
        self.client.login(username="student_submit", password="password123")

    def test_six_catalogue_labels_persist_as_six_strings(self):
        labels = [
            "Business Model Canvas",
            "Market Validation",
            "Startup Financing",
            "Intellectual Property",
            "Digital Marketing",
            "Risk Management",
        ]
        resp = self.client.post("/focus/checkin/", {
            "subject_name": "Technopreneurship",
            "subject_code": "TECHNO",
            "topic_names": ", ".join(labels),
            "reason": "school_result",
        })
        self.assertEqual(resp.status_code, 302)

        focus = LearningFocus.objects.get(user=self.user)
        self.assertEqual(focus.topic_names, labels)
        self.assertTrue(all(isinstance(t, str) for t in focus.topic_names))
        self.assertEqual(focus.status, LearningFocus.STATUS_ACTIVE)
        self.assertEqual(focus.source_payload.get("reason"), "school_result")

    def test_subject_only_submission_with_no_topics(self):
        resp = self.client.post("/focus/checkin/", {
            "subject_name": "Calculus",
            "topic_names": "",
        })
        self.assertEqual(resp.status_code, 302)

        focus = LearningFocus.objects.get(user=self.user)
        self.assertEqual(focus.topic_names, [])
        self.assertEqual(focus.subject_name, "Calculus")

    def test_empty_submission_creates_no_focus(self):
        resp = self.client.post("/focus/checkin/", {"subject_name": "", "topic_names": ""})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(LearningFocus.objects.filter(user=self.user).exists())

    def test_manual_reason_values_are_stable_machine_values(self):
        """The select's option values are the ones the server stores."""
        resp = self.client.get("/")
        body = resp.content.decode()
        for value in (
            "low_result",
            "upcoming_exam",
            "difficult_topic",
            "instructor_recommendation",
            "extra_practice",
            "school_result",
        ):
            self.assertIn(f'value="{value}"', body)
        # The dead display-string value must be gone.
        self.assertNotIn("School assessment result", body)




