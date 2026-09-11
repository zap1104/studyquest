from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.dashboard_service import (
    get_dashboard_next_action,
    get_recent_active_courses,
    get_streak_status,
)
from courses.models import (
    Chapter,
    ChapterCompletion,
    Course,
    Quiz,
    QuizAttempt,
    UserProfile,
)


class StreakStatusUnitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="streak_tester", password="password123")
        self.profile = UserProfile.objects.get(user=self.user)

    def test_streak_zero(self):
        self.profile.streak_days = 0
        self.profile.last_study_date = None
        self.profile.save()

        status = get_streak_status(self.profile)
        self.assertEqual(status["status"], "zero")
        self.assertFalse(status["studied_today"])
        self.assertEqual(status["streak"], 0)
        self.assertEqual(status["badge_label"], "Start your streak")
        self.assertIn("begin a study streak", status["message"])

    def test_streak_pending_today(self):
        today = timezone.now().date()
        self.profile.streak_days = 4
        self.profile.last_study_date = today - timedelta(days=1)
        self.profile.save()

        status = get_streak_status(self.profile)
        self.assertEqual(status["status"], "pending")
        self.assertFalse(status["studied_today"])
        self.assertEqual(status["streak"], 4)
        self.assertEqual(status["badge_label"], "Action needed today")
        self.assertIn("continue your 4-day streak", status["message"])

    def test_streak_secured_today(self):
        today = timezone.now().date()
        self.profile.streak_days = 5
        self.profile.last_study_date = today
        self.profile.save()

        status = get_streak_status(self.profile)
        self.assertEqual(status["status"], "secured")
        self.assertTrue(status["studied_today"])
        self.assertEqual(status["streak"], 5)
        self.assertEqual(status["badge_label"], "Secured today")
        self.assertIn("5-day streak secured for today", status["message"])

    def test_none_profile_handled_safely(self):
        status = get_streak_status(None)
        self.assertEqual(status["status"], "zero")
        self.assertFalse(status["studied_today"])


class DashboardNextActionUnitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="action_tester", password="password123")
        self.profile = UserProfile.objects.get(user=self.user)

    def test_state_no_courses(self):
        action = get_dashboard_next_action(self.user)
        self.assertEqual(action["state"], "no_courses")
        self.assertIsNone(action["course"])
        self.assertIsNone(action["chapter"])
        self.assertEqual(action["button_url"], reverse("courses:course_create"))
        self.assertIn("Create Your First Course", action["button_label"])

    def test_state_reading_not_started(self):
        course = Course.objects.create(user=self.user, title="Physics 101", status="active")
        ch1 = Chapter.objects.create(course=course, order=1, title="Kinematics")
        Quiz.objects.create(chapter=ch1, title="Kinematics Quiz")

        action = get_dashboard_next_action(self.user)
        self.assertEqual(action["state"], "reading_not_started")
        self.assertEqual(action["course"], course)
        self.assertEqual(action["chapter"], ch1)
        self.assertEqual(action["primary_button_url"], reverse("courses:chapter_review", args=[ch1.pk]))
        self.assertIn("Continue Reading", action["primary_button_label"])
        self.assertFalse(action["secondary_button_disabled"])
        self.assertIn("Try Quiz First", action["secondary_button_label"])
        self.assertEqual(action["progress_pct"], 0)

    def test_state_reading_required_when_quiz_scored_between_75_and_89_without_reading(self):
        course = Course.objects.create(user=self.user, title="Physics 101", status="active")
        ch1 = Chapter.objects.create(course=course, order=1, title="Kinematics")
        quiz = Quiz.objects.create(chapter=ch1, title="Kinematics Quiz")

        # 3/4 = 75% score, but no ChapterCompletion
        QuizAttempt.objects.create(
            user=self.user,
            quiz=quiz,
            score=3,
            total_questions=4,
            xp_earned=35,
            review_data={"percentage": 75, "passed": True},
        )

        action = get_dashboard_next_action(self.user)
        self.assertEqual(action["state"], "reading_required_for_pass")
        self.assertEqual(action["chapter"], ch1)
        self.assertIn("Complete Chapter 1 Reading", action["primary_button_label"])
        self.assertIn("Retake Quiz", action["secondary_button_label"])
        self.assertFalse(action["secondary_button_disabled"])
        self.assertIn("Scored 75% on quiz", action["status_text"])

    def test_advances_to_subsequent_chapter_via_prior_knowledge_without_reading(self):
        course = Course.objects.create(user=self.user, title="Chemistry 101", status="active")
        ch1 = Chapter.objects.create(course=course, order=1, title="Atoms")
        quiz1 = Quiz.objects.create(chapter=ch1, title="Atoms Quiz")
        ch2 = Chapter.objects.create(course=course, order=2, title="Bonds")
        Quiz.objects.create(chapter=ch2, title="Bonds Quiz")

        # Pass Chapter 1 quiz with 100% (>= 90%) without ChapterCompletion
        QuizAttempt.objects.create(
            user=self.user,
            quiz=quiz1,
            score=4,
            total_questions=4,
            xp_earned=60,
            review_data={"percentage": 100, "passed": True},
        )

        action = get_dashboard_next_action(self.user)
        self.assertEqual(action["state"], "reading_not_started")
        self.assertEqual(action["chapter"], ch2)
        self.assertEqual(action["progress_pct"], 50)
        self.assertEqual(action["completed_chapters"], 1)
        self.assertEqual(action["total_chapters"], 2)

    def test_state_quiz_ready_when_reading_complete_and_quiz_not_taken(self):
        course = Course.objects.create(user=self.user, title="Physics 101", status="active")
        ch1 = Chapter.objects.create(course=course, order=1, title="Kinematics")
        Quiz.objects.create(chapter=ch1, title="Kinematics Quiz")
        ChapterCompletion.objects.create(user=self.user, chapter=ch1)

        action = get_dashboard_next_action(self.user)
        self.assertEqual(action["state"], "quiz_ready")
        self.assertEqual(action["course"], course)
        self.assertEqual(action["chapter"], ch1)
        self.assertEqual(action["primary_button_url"], reverse("courses:chapter_quiz", args=[ch1.pk]))
        self.assertIn("Take Chapter 1 Quiz", action["primary_button_label"])
        self.assertFalse(action["secondary_button_disabled"])
        self.assertEqual(action["secondary_button_url"], reverse("courses:chapter_review", args=[ch1.pk]))

    def test_state_quiz_not_passed_retake_guidance(self):
        course = Course.objects.create(user=self.user, title="Physics 101", status="active")
        ch1 = Chapter.objects.create(course=course, order=1, title="Kinematics")
        quiz = Quiz.objects.create(chapter=ch1, title="Kinematics Quiz")
        ChapterCompletion.objects.create(user=self.user, chapter=ch1)

        # Attempt with 50% score (threshold is 75%)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=quiz,
            score=2,
            total_questions=4,
            xp_earned=20,
            review_data={"percentage": 50, "passed": False},
        )

        action = get_dashboard_next_action(self.user)
        self.assertEqual(action["state"], "quiz_not_passed")
        self.assertEqual(action["chapter"], ch1)
        self.assertEqual(action["button_url"], reverse("courses:chapter_quiz", args=[ch1.pk]))
        self.assertIn("Retake", action["button_label"])
        self.assertIn("50%", action["status_text"])
        self.assertIn("passing score (≥ 75%)", action["status_text"])

    def test_advances_to_subsequent_chapter(self):
        course = Course.objects.create(user=self.user, title="Chemistry 101", status="active")
        ch1 = Chapter.objects.create(course=course, order=1, title="Atoms")
        quiz1 = Quiz.objects.create(chapter=ch1, title="Atoms Quiz")
        ch2 = Chapter.objects.create(course=course, order=2, title="Bonds")
        Quiz.objects.create(chapter=ch2, title="Bonds Quiz")

        # Pass Chapter 1 quiz with 100%
        ChapterCompletion.objects.create(user=self.user, chapter=ch1)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=quiz1,
            score=4,
            total_questions=4,
            xp_earned=60,
            review_data={"percentage": 100, "passed": True},
        )

        action = get_dashboard_next_action(self.user)
        self.assertEqual(action["state"], "reading_not_started")
        self.assertEqual(action["chapter"], ch2)
        self.assertEqual(action["button_url"], reverse("courses:chapter_review", args=[ch2.pk]))
        self.assertEqual(action["progress_pct"], 50)
        self.assertEqual(action["completed_chapters"], 1)
        self.assertEqual(action["total_chapters"], 2)

    def test_state_all_courses_completed(self):
        course = Course.objects.create(user=self.user, title="Biology 101", status="active")
        ch1 = Chapter.objects.create(course=course, order=1, title="Cells")
        quiz1 = Quiz.objects.create(chapter=ch1, title="Cells Quiz")

        ChapterCompletion.objects.create(user=self.user, chapter=ch1)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=quiz1,
            score=4,
            total_questions=4,
            xp_earned=60,
            review_data={"percentage": 100, "passed": True},
        )

        action = get_dashboard_next_action(self.user)
        self.assertEqual(action["state"], "all_courses_completed")
        self.assertEqual(action["course"], course)
        self.assertEqual(action["progress_pct"], 100)
        self.assertEqual(action["button_url"], reverse("courses:course_create"))
        self.assertIn("Create New Course", action["button_label"])

    def test_last_opened_course_priority(self):
        course_a = Course.objects.create(user=self.user, title="Course A", status="active")
        ch_a = Chapter.objects.create(course=course_a, order=1, title="A1")

        course_b = Course.objects.create(user=self.user, title="Course B", status="active")
        Chapter.objects.create(course=course_b, order=1, title="B1")

        # By updated_at alone, course_b would win since it was created last.
        # But if user last opened course_a:
        self.profile.last_opened_course = course_a
        self.profile.last_opened_chapter = ch_a
        self.profile.save()
        self.user.refresh_from_db()

        action = get_dashboard_next_action(self.user)
        self.assertEqual(action["course"], course_a)
        self.assertEqual(action["chapter"], ch_a)


class RecentActiveCoursesUnitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="recent_tester", password="password123")

    def test_recent_courses_exclude_and_limit(self):
        c1 = Course.objects.create(user=self.user, title="C1", status="active")
        c2 = Course.objects.create(user=self.user, title="C2", status="active")
        c3 = Course.objects.create(user=self.user, title="C3", status="active")
        c4 = Course.objects.create(user=self.user, title="C4", status="active")
        c_archived = Course.objects.create(user=self.user, title="C_Archived", status="archived")

        recent = get_recent_active_courses(self.user, limit=3, exclude_course_id=c4.pk)
        self.assertEqual(len(recent), 3)
        recent_ids = [c.pk for c in recent]
        self.assertNotIn(c4.pk, recent_ids)
        self.assertNotIn(c_archived.pk, recent_ids)

        # Confirm progression attributes attached
        for c in recent:
            self.assertTrue(hasattr(c, "progress_pct"))
            self.assertTrue(hasattr(c, "completed_chapter_count"))
            self.assertTrue(hasattr(c, "total_chapter_count"))


class DashboardIntegrationAndTrackingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="learner", password="password123")
        self.profile = UserProfile.objects.get(user=self.user)
        self.course = Course.objects.create(user=self.user, title="Computer Systems", status="active")
        self.ch1 = Chapter.objects.create(course=self.course, order=1, title="Logic Gates")
        self.quiz1 = Quiz.objects.create(chapter=self.ch1, title="Logic Gates Quiz")

    def test_unauthenticated_dashboard_redirects(self):
        response = self.client.get(reverse("courses:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_authenticated_dashboard_renders_zones(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("courses:dashboard"))
        self.assertEqual(response.status_code, 200)

        # Zone 1 Greeting & Streak
        self.assertContains(response, "Welcome back, learner")
        self.assertContains(response, "streak-status-pill")
        self.assertContains(response, "begin a study streak")

        # Zone 2 Next In Course & Momentum
        self.assertContains(response, "Next in Your Course")
        self.assertContains(response, "Computer Systems")
        self.assertContains(response, "Logic Gates")
        self.assertContains(response, "course-progress-summary")
        self.assertContains(response, "This Week")
        self.assertContains(response, "Chapters Completed")
        self.assertContains(response, "Rank &amp; Tiers")
        self.assertContains(response, "Next Achievement")

        # With only 1 course (which is featured), Recent Courses section is cleanly suppressed
        self.assertNotContains(response, "recent-courses-grid")
        self.assertNotContains(response, '<section class="dashboard-recent-courses-section"')

        # When a second course is added, Recent Courses section appears
        Course.objects.create(user=self.user, title="Operating Systems", status="active")
        res2 = self.client.get(reverse("courses:dashboard"))
        self.assertContains(res2, "Recent Courses")
        self.assertContains(res2, "Operating Systems")

    def test_chapter_review_tracks_last_opened(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("courses:chapter_review", args=[self.ch1.pk]))
        self.assertEqual(response.status_code, 200)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_opened_course, self.course)
        self.assertEqual(self.profile.last_opened_chapter, self.ch1)

    def test_chapter_quiz_tracks_last_opened(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("courses:chapter_quiz", args=[self.ch1.pk]))
        self.assertEqual(response.status_code, 200)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_opened_course, self.course)
        self.assertEqual(self.profile.last_opened_chapter, self.ch1)

