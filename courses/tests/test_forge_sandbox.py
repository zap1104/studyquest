from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from courses.models import Course, UserProfile


class ForgeSandboxViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="student_tester",
            email="tester@example.com",
            password="testpass123",
        )
        self.staff_user = User.objects.create_user(
            username="staff_developer",
            email="staff@example.com",
            password="testpass123",
            is_staff=True,
        )
        self.url = reverse("courses:forge_sandbox")

    @override_settings(DEBUG=False, STUDYQUEST_FORGE_SANDBOX_ENABLED=False)
    def test_anonymous_user_receives_404_when_debug_and_flag_disabled(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    @override_settings(DEBUG=True, STUDYQUEST_FORGE_SANDBOX_ENABLED=False)
    def test_anonymous_user_redirected_to_login_when_debug_true(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)
        self.assertIn("next=/courses/forge/sandbox/", response.url)

    @override_settings(DEBUG=False, STUDYQUEST_FORGE_SANDBOX_ENABLED=False)
    def test_unauthorized_user_receives_404_when_debug_and_flag_disabled(self):
        self.client.login(username="student_tester", password="testpass123")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    @override_settings(DEBUG=True, STUDYQUEST_FORGE_SANDBOX_ENABLED=False)
    def test_authenticated_user_access_permitted_when_debug_true(self):
        self.client.login(username="student_tester", password="testpass123")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "courses/forge_sandbox.html")

    @override_settings(DEBUG=False, STUDYQUEST_FORGE_SANDBOX_ENABLED=False)
    def test_staff_user_access_permitted_even_when_debug_false(self):
        self.client.login(username="staff_developer", password="testpass123")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "courses/forge_sandbox.html")

    @override_settings(DEBUG=False, STUDYQUEST_FORGE_SANDBOX_ENABLED=True)
    def test_authenticated_user_access_permitted_when_flag_enabled(self):
        self.client.login(username="student_tester", password="testpass123")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "courses/forge_sandbox.html")

    @override_settings(DEBUG=True)
    def test_sandbox_maintains_strict_database_isolation(self):
        self.client.login(username="student_tester", password="testpass123")

        initial_courses_count = Course.objects.count()
        profile = UserProfile.objects.get(user=self.user)
        initial_xp = profile.total_xp
        initial_level = profile.current_level
        initial_bonus = profile.bonus_course_credits

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        # 0 Course rows created
        self.assertEqual(Course.objects.count(), initial_courses_count)

        # User profile gamification stats unchanged
        profile.refresh_from_db()
        self.assertEqual(profile.total_xp, initial_xp)
        self.assertEqual(profile.current_level, initial_level)
        self.assertEqual(profile.bonus_course_credits, initial_bonus)

    @override_settings(DEBUG=True)
    def test_sandbox_template_context_and_markup(self):
        self.client.login(username="student_tester", password="testpass123")
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "sandbox")
        self.assertEqual(response.context["course_id"], "sandbox")
        self.assertEqual(response.context["generation_status"], "testing")

        content = response.content.decode("utf-8")

        # Isolated sandbox attributes and storage namespace reference
        self.assertIn("data-camp-root", content)
        self.assertIn('data-course-id="sandbox"', content)
        self.assertIn("studyquest:forge:sandbox", content)

        # Developer controls presence
        self.assertIn("data-sandbox-controls", content)
        self.assertIn("data-sandbox-add-wood", content)
        self.assertIn("data-sandbox-add-crystals", content)
        self.assertIn("data-sandbox-simulate-ready", content)
        self.assertIn("data-sandbox-pause-resume", content)
        self.assertIn("data-sandbox-toggle-mute", content)
        self.assertIn("data-sandbox-show-summary", content)
        self.assertIn("data-sandbox-reset", content)

        # Scripts loaded
        self.assertIn("forge_state.js", content)
        self.assertIn("forge_input.js", content)
        self.assertIn("forge_camera.js", content)
        self.assertIn("forge_spatial.js", content)
        self.assertIn("forge_entities.js", content)
        self.assertIn("forge_plots.js", content)
        self.assertIn("forge_renderer.js", content)
        self.assertIn("forge_camp.js", content)
        self.assertIn("forge_sandbox.js", content)


class ForgeSandboxEasterEggHeaderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="student_tester",
            password="testpass123",
        )
        self.staff_user = User.objects.create_user(
            username="staff_developer",
            password="testpass123",
            is_staff=True,
        )

    def test_header_contains_brand_link_and_sandbox_elements(self):
        self.client.login(username="student_tester", password="testpass123")
        response = self.client.get(reverse("courses:dashboard"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")

        # 5-click easter egg anchors & elements
        self.assertIn("data-brand-link", content)
        self.assertIn('data-sandbox-url="/courses/forge/sandbox/"', content)
        self.assertIn('id="sandbox-announcer"', content)
        self.assertIn('id="sandbox-toast"', content)
        self.assertIn("studyquest_sandbox_unlock.js", content)

        # Non-staff users do not see visible sandbox navigation links
        self.assertNotIn("dev-sandbox-link", content)

    def test_staff_user_sees_unobtrusive_nav_links(self):
        self.client.login(username="staff_developer", password="testpass123")
        response = self.client.get(reverse("courses:dashboard"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        self.assertIn("dev-sandbox-link", content)
        self.assertIn("Camp Sandbox (Dev)", content)

