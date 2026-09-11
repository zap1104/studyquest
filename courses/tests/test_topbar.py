from datetime import timedelta
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase
from django.utils import timezone

from courses.context_processors import topbar_stats
from courses.models import UserProfile, XPTransaction


class TopbarStatsContextProcessorTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="quest_hero", password="password123")
        self.profile = self.user.userprofile
        self.profile.total_xp = 145
        self.profile.current_level = 2
        self.profile.streak_days = 3
        self.profile.best_streak_days = 5
        self.profile.save()

    def test_anonymous_user_returns_none(self):
        request = self.factory.get("/")
        request.user = AnonymousUser()
        context = topbar_stats(request)
        self.assertIn("topbar_stats", context)
        self.assertIsNone(context["topbar_stats"])

    def test_authenticated_user_returns_structured_stats(self):
        request = self.factory.get("/")
        request.user = self.user
        context = topbar_stats(request)

        self.assertIn("topbar_stats", context)
        data = context["topbar_stats"]
        self.assertIsNotNone(data)

        # Check level & tier
        self.assertEqual(data["tier"]["name"], "Bronze")
        self.assertEqual(data["level_progress"]["current_level"], 2)
        self.assertEqual(data["level_progress"]["next_level"], 3)
        self.assertEqual(data["level_progress"]["xp_into_level"], 45)
        self.assertEqual(data["level_progress"]["xp_needed_for_next"], 55)

        # Check 7-day week calendar
        week = data["streak_week"]
        self.assertEqual(len(week), 7)
        labels = [day["label"] for day in week]
        self.assertEqual(labels, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])

        # Exactly one day in the week is marked is_today
        today_days = [day for day in week if day["is_today"]]
        self.assertEqual(len(today_days), 1)

    def test_streak_today_pending_vs_secured(self):
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)

        # Case 1: Studied yesterday (streak is pending today)
        self.profile.last_study_date = yesterday
        self.profile.streak_days = 2
        self.profile.save()

        request = self.factory.get("/")
        request.user = self.user
        context = topbar_stats(request)
        week = context["topbar_stats"]["streak_week"]

        today_item = next(day for day in week if day["is_today"])
        self.assertEqual(today_item["status"], "today_pending")
        self.assertFalse(context["topbar_stats"]["streak_status"]["studied_today"])

        # Case 2: Studied today (streak secured for today)
        self.profile.last_study_date = today
        self.profile.streak_days = 3
        self.profile.save()

        context2 = topbar_stats(request)
        week2 = context2["topbar_stats"]["streak_week"]
        today_item2 = next(day for day in week2 if day["is_today"])
        self.assertEqual(today_item2["status"], "today_done")
        self.assertTrue(context2["topbar_stats"]["streak_status"]["studied_today"])

    def test_recent_xp_and_weekly_xp_aggregation(self):
        # Create XP transactions
        XPTransaction.objects.create(user=self.user, amount=50, reason="Passed Chapter 1 Quiz")
        XPTransaction.objects.create(user=self.user, amount=10, reason="Completed Chapter 1 Reading")

        request = self.factory.get("/")
        request.user = self.user
        context = topbar_stats(request)
        data = context["topbar_stats"]

        self.assertGreaterEqual(data["weekly_xp"], 60)
        self.assertEqual(len(data["recent_xp"]), 2)
        self.assertEqual(data["recent_xp"][0].amount, 10)
        self.assertEqual(data["recent_xp"][1].amount, 50)


class TopbarTemplateIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="template_tester", password="password123")

    def test_topbar_popovers_rendered_in_dashboard(self):
        from django.urls import reverse
        self.client.login(username="template_tester", password="password123")
        response = self.client.get(reverse("courses:dashboard"))
        self.assertEqual(response.status_code, 200)

        # Check interactive buttons exist
        self.assertContains(response, 'id="topbar-level-btn"')
        self.assertContains(response, 'id="topbar-xp-btn"')
        self.assertContains(response, 'id="topbar-streak-btn"')

        # Check popover panels exist
        self.assertContains(response, 'id="topbar-level-popover"')
        self.assertContains(response, 'id="topbar-xp-popover"')
        self.assertContains(response, 'id="topbar-streak-popover"')
        self.assertContains(response, 'streak-calendar-strip')
