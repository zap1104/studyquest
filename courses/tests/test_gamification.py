from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.gamification import (
    get_achievement_preview,
    get_active_course_progress,
    get_leaderboard_standings,
    get_level_progress,
    get_tier_info,
    get_tier_progress,
    get_user_achievements,
    get_weekly_momentum,
)
from courses.models import (
    Chapter,
    ChapterCompletion,
    Course,
    Question,
    Quiz,
    QuizAttempt,
    UserCourseCompletion,
    UserProfile,
    XPTransaction,
)


class UserProfileStreakTrackingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="streak_user", password="secretpassword123")
        self.profile = UserProfile.objects.get(user=self.user)

    def test_streak_and_best_streak_progression(self):
        self.assertEqual(self.profile.streak_days, 0)
        self.assertEqual(self.profile.best_streak_days, 0)

        # Day 1
        today = timezone.now().date()
        self.profile.last_study_date = today - timedelta(days=2)
        self.profile.update_streak()
        self.assertEqual(self.profile.streak_days, 1)
        self.assertEqual(self.profile.best_streak_days, 1)

        # Day 2 consecutive
        self.profile.last_study_date = today - timedelta(days=1)
        self.profile.update_streak()
        self.assertEqual(self.profile.streak_days, 2)
        self.assertEqual(self.profile.best_streak_days, 2)

        # Streak broken after missing days
        self.profile.last_study_date = today - timedelta(days=4)
        self.profile.update_streak()
        self.assertEqual(self.profile.streak_days, 1)
        # Best streak must NEVER decrease
        self.assertEqual(self.profile.best_streak_days, 2)

        # Streak rises past previous best
        self.profile.streak_days = 2
        self.profile.last_study_date = today - timedelta(days=1)
        self.profile.update_streak()
        self.assertEqual(self.profile.streak_days, 3)
        self.assertEqual(self.profile.best_streak_days, 3)


class GamificationMathAndTierTests(TestCase):
    def test_tier_threshold_rules(self):
        self.assertEqual(get_tier_info(1)["name"], "Bronze")
        self.assertEqual(get_tier_info(4)["name"], "Bronze")
        self.assertEqual(get_tier_info(5)["name"], "Silver")
        self.assertEqual(get_tier_info(9)["name"], "Silver")
        self.assertEqual(get_tier_info(10)["name"], "Gold")
        self.assertEqual(get_tier_info(19)["name"], "Gold")
        self.assertEqual(get_tier_info(20)["name"], "Platinum")
        self.assertEqual(get_tier_info(42)["name"], "Platinum")

    def test_level_progress_math(self):
        # Level 1, 65 XP -> 65% through level
        prog1 = get_level_progress(total_xp=65, current_level=1)
        self.assertEqual(prog1["current_level_floor"], 0)
        self.assertEqual(prog1["next_level_threshold"], 100)
        self.assertEqual(prog1["xp_into_level"], 65)
        self.assertEqual(prog1["xp_needed_for_next"], 35)
        self.assertEqual(prog1["progress_pct"], 65)

        # Level 5, 430 XP -> 30% through level 5 (floor 400, next 500)
        prog5 = get_level_progress(total_xp=430, current_level=5)
        self.assertEqual(prog5["current_level_floor"], 400)
        self.assertEqual(prog5["next_level_threshold"], 500)
        self.assertEqual(prog5["xp_into_level"], 30)
        self.assertEqual(prog5["xp_needed_for_next"], 70)
        self.assertEqual(prog5["progress_pct"], 30)

    def test_tier_progress_math(self):
        # Level 3, 250 XP: Bronze tier, next is Silver (Lv 5, 400 XP)
        tier_prog = get_tier_progress(current_level=3, total_xp=250)
        self.assertEqual(tier_prog["current_tier"]["name"], "Bronze")
        self.assertEqual(tier_prog["next_tier"]["name"], "Silver")
        self.assertEqual(tier_prog["levels_to_next"], 2)
        self.assertEqual(tier_prog["xp_to_next_tier"], 150)  # 400 - 250 = 150

        # Platinum tier (Max)
        plat_prog = get_tier_progress(current_level=20, total_xp=2100)
        self.assertEqual(plat_prog["current_tier"]["name"], "Platinum")
        self.assertIsNone(plat_prog["next_tier"])
        self.assertEqual(plat_prog["tier_progress_pct"], 100)


class WeeklyMomentumAndAchievementsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="questor", password="password123")
        self.profile = UserProfile.objects.get(user=self.user)

    def test_weekly_momentum_filters_to_current_calendar_week(self):
        now = timezone.now()
        start_of_week = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Transaction inside this week
        XPTransaction.objects.create(
            user=self.user,
            amount=50,
            reason="Current week quiz",
            created_at=start_of_week + timedelta(hours=2),
        )
        # Transaction from prior week
        old_tx = XPTransaction.objects.create(
            user=self.user,
            amount=100,
            reason="Old week quiz",
        )
        # Force old timestamp via update
        XPTransaction.objects.filter(pk=old_tx.pk).update(
            created_at=start_of_week - timedelta(days=5)
        )

        momentum = get_weekly_momentum(self.user)
        self.assertEqual(momentum["weekly_xp"], 50)

    def test_achievements_evaluation(self):
        # 1. Initially all locked
        ach = get_user_achievements(self.user)
        self.assertEqual(ach["unlocked_count"], 0)
        self.assertEqual(ach["total_count"], 6)

        # 2. Create course -> First Course unlocks
        course = Course.objects.create(user=self.user, title="Techno 101")
        ach = get_user_achievements(self.user)
        self.assertEqual(ach["unlocked_count"], 1)
        self.assertTrue(next(a["unlocked"] for a in ach["list"] if a["id"] == "first_course"))

        # 3. Read chapter -> First Chapter unlocks
        chapter = Chapter.objects.create(course=course, order=1, title="Chapter 1")
        ChapterCompletion.objects.create(user=self.user, chapter=chapter)
        ach = get_user_achievements(self.user)
        self.assertEqual(ach["unlocked_count"], 2)
        self.assertTrue(next(a["unlocked"] for a in ach["list"] if a["id"] == "first_chapter"))

        # 4. Pass quiz with 100% -> Quiz Cleared and Flawless both unlock
        quiz = Quiz.objects.create(chapter=chapter, title="Quiz 1")
        QuizAttempt.objects.create(
            user=self.user,
            quiz=quiz,
            score=4,
            total_questions=4,
            xp_earned=25,
            review_data={"percentage": 100, "passed": True},
        )
        ach = get_user_achievements(self.user)
        self.assertEqual(ach["unlocked_count"], 4)
        self.assertTrue(next(a["unlocked"] for a in ach["list"] if a["id"] == "quiz_cleared"))
        self.assertTrue(next(a["unlocked"] for a in ach["list"] if a["id"] == "flawless_quiz"))

        # 5. Streak >= 7 -> Seven-Day Streak unlocks
        self.profile.best_streak_days = 7
        self.profile.save()
        ach = get_user_achievements(self.user)
        self.assertEqual(ach["unlocked_count"], 5)
        self.assertTrue(next(a["unlocked"] for a in ach["list"] if a["id"] == "seven_day_streak"))

        # 6. Complete course -> Course Graduate unlocks
        UserCourseCompletion.objects.create(user=self.user, course=course)
        ach = get_user_achievements(self.user)
        self.assertEqual(ach["unlocked_count"], 6)
        self.assertTrue(next(a["unlocked"] for a in ach["list"] if a["id"] == "course_graduate"))


class GamificationViewsIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alexa_learner", password="password123")
        self.peer = User.objects.create_user(username="jendrick_peer", password="password123")
        self.profile = UserProfile.objects.get(user=self.user)
        self.peer_profile = UserProfile.objects.get(user=self.peer)

        self.peer_profile.total_xp = 500
        self.peer_profile.current_level = 6
        self.peer_profile.save()

        self.profile.total_xp = 250
        self.profile.current_level = 3
        self.profile.save()

    def test_unauthenticated_requests_redirect(self):
        res_profile = self.client.get(reverse("courses:profile"))
        self.assertEqual(res_profile.status_code, 302)
        self.assertIn("login", res_profile.url)

        res_lead = self.client.get(reverse("courses:leaderboard"))
        self.assertEqual(res_lead.status_code, 302)
        self.assertIn("login", res_lead.url)

    def test_authenticated_profile_view(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("courses:profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alexa_learner")
        self.assertContains(response, "Bronze Tier")
        self.assertContains(response, "Active Courses")
        self.assertContains(response, "Achievements")

    def test_authenticated_leaderboard_view(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("courses:leaderboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Academic Ranks")
        self.assertContains(response, "Tier Progression Track")
        self.assertContains(response, "Momentum")
        # Both learners should appear in standings with authentic ranks
        self.assertContains(response, "jendrick_peer")
        self.assertContains(response, "alexa_learner")
        self.assertContains(response, "You")


class AchievementPreviewUnitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="milestone_user", password="password123")
        self.profile = UserProfile.objects.get(user=self.user)

    def test_milestone_zero_courses(self):
        preview = get_achievement_preview(self.user)
        self.assertEqual(preview["key"], "first_course")
        self.assertEqual(preview["icon"], "📜")
        self.assertIn("Synthesize your first review journey", preview["instruction"])
        self.assertFalse(preview["is_earned"])
        self.assertEqual(preview["progress_pct"], 0)

    def test_milestone_course_created_needs_chapter(self):
        course = Course.objects.create(user=self.user, title="Data Structures")
        Chapter.objects.create(course=course, order=1, title="Arrays")

        preview = get_achievement_preview(self.user)
        self.assertEqual(preview["key"], "first_chapter")
        self.assertEqual(preview["icon"], "📖")
        self.assertIn("first chapter review reading", preview["instruction"])

    def test_milestone_chapter_read_needs_quiz(self):
        course = Course.objects.create(user=self.user, title="Data Structures")
        ch1 = Chapter.objects.create(course=course, order=1, title="Arrays")
        ChapterCompletion.objects.create(user=self.user, chapter=ch1)

        preview = get_achievement_preview(self.user)
        self.assertEqual(preview["key"], "quiz_cleared")
        self.assertEqual(preview["icon"], "⚡")
        self.assertIn("≥ 75%", preview["instruction"])

    def test_milestone_in_progress_course_graduate(self):
        course = Course.objects.create(user=self.user, title="Data Structures", status="active")
        ch1 = Chapter.objects.create(course=course, order=1, title="Arrays")
        quiz1 = Quiz.objects.create(chapter=ch1, title="Quiz 1")
        ch2 = Chapter.objects.create(course=course, order=2, title="Trees")
        Quiz.objects.create(chapter=ch2, title="Quiz 2")
        ch3 = Chapter.objects.create(course=course, order=3, title="Graphs")
        Quiz.objects.create(chapter=ch3, title="Quiz 3")

        ChapterCompletion.objects.create(user=self.user, chapter=ch1)
        QuizAttempt.objects.create(
            user=self.user,
            quiz=quiz1,
            score=4,
            total_questions=4,
            xp_earned=60,
            review_data={"percentage": 100, "passed": True},
        )

        preview = get_achievement_preview(self.user)
        self.assertEqual(preview["key"], "course_graduate")
        self.assertEqual(preview["icon"], "🎓")
        self.assertIn("Complete 2 more chapters", preview["instruction"])
        self.assertEqual(preview["current"], 1)
        self.assertEqual(preview["target"], 3)
        self.assertEqual(preview["progress_pct"], 33)
        self.assertIn("2 chapters remaining", preview["remaining_text"])

