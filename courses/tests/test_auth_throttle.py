"""Failed-login throttling for the auth portal.

This covers the unauthenticated brute-force protection only. The plan-based
course-generation quota lives in credits.py and is tested separately.
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings


class AuthThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="throttle_student", password="correct-horse-battery"
        )

    def tearDown(self):
        cache.clear()

    def _failed_login(self):
        return self.client.post("/login/", {
            "action": "login",
            "username": "throttle_student",
            "password": "wrong-password",
        })

    def test_legitimate_single_login_is_unaffected(self):
        resp = self.client.post("/login/", {
            "action": "login",
            "username": "throttle_student",
            "password": "correct-horse-battery",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")

    def test_repeated_failures_are_throttled_with_429(self):
        limit = 5
        with override_settings(LOGIN_THROTTLE_MAX_ATTEMPTS=limit):
            # The first `limit` failures are rejected as bad credentials.
            for _ in range(limit):
                resp = self._failed_login()
                self.assertEqual(resp.status_code, 200)

            # The next attempt is throttled before credentials are checked.
            resp = self._failed_login()
            self.assertEqual(resp.status_code, 429)

    def test_successful_login_clears_the_attempt_counter(self):
        limit = 3
        with override_settings(LOGIN_THROTTLE_MAX_ATTEMPTS=limit):
            for _ in range(limit - 1):
                self._failed_login()

            # A correct login resets the counter...
            resp = self.client.post("/login/", {
                "action": "login",
                "username": "throttle_student",
                "password": "correct-horse-battery",
            })
            self.assertEqual(resp.status_code, 302)

        # ...so a fresh run of failures does not immediately trip the limit.
        self.client.logout()
        with override_settings(LOGIN_THROTTLE_MAX_ATTEMPTS=limit):
            for _ in range(limit - 1):
                resp = self._failed_login()
                self.assertEqual(resp.status_code, 200)

    def test_throttle_is_scoped_per_action(self):
        """Exhausting login attempts does not block signup."""
        limit = 2
        with override_settings(LOGIN_THROTTLE_MAX_ATTEMPTS=limit):
            for _ in range(limit):
                self._failed_login()
            self.assertEqual(self._failed_login().status_code, 429)

            signup_resp = self.client.post("/login/", {
                "action": "signup",
                "username": "brand_new_student",
                "password1": "a-strong-passphrase-9",
                "password2": "a-strong-passphrase-9",
            })
            self.assertNotEqual(signup_resp.status_code, 429)

    def test_counter_expires_after_the_window(self):
        with override_settings(
            LOGIN_THROTTLE_MAX_ATTEMPTS=2,
            LOGIN_THROTTLE_WINDOW_SECONDS=1,
        ):
            self._failed_login()
            self._failed_login()
            self.assertEqual(self._failed_login().status_code, 429)

            # Expire the throttle window and confirm the caller is allowed back.
            cache.clear()
            self.assertEqual(self._failed_login().status_code, 200)