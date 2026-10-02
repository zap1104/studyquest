"""Failed-login throttling for the auth portal.

This covers the unauthenticated brute-force protection only. The plan-based
course-generation quota lives in credits.py and is tested separately.
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from courses.views import _client_ip


class AuthThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="throttle_student", password="correct-horse-battery"
        )

    def tearDown(self):
        cache.clear()

    def _failed_login(self, **kwargs):
        return self.client.post("/login/", {
            "action": "login",
            "username": "throttle_student",
            "password": "wrong-password",
        }, **kwargs)

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

    def test_spoofed_forwarded_for_cannot_bypass_login_throttle(self):
        """Rotating X-Forwarded-For header values does not bypass login throttling."""
        limit = 3
        with override_settings(LOGIN_THROTTLE_MAX_ATTEMPTS=limit):
            # Attempt login failures with different spoofed IPs in each request
            for i in range(limit):
                resp = self.client.post(
                    "/login/",
                    {
                        "action": "login",
                        "username": "throttle_student",
                        "password": "wrong-password",
                    },
                    HTTP_X_FORWARDED_FOR=f"198.51.100.{i + 1}",
                )
                self.assertEqual(resp.status_code, 200)

            # Next attempt from the same client is throttled, even with a new spoofed header
            resp = self.client.post(
                "/login/",
                {
                    "action": "login",
                    "username": "throttle_student",
                    "password": "wrong-password",
                },
                HTTP_X_FORWARDED_FOR="198.51.100.99",
            )
            self.assertEqual(resp.status_code, 429)

    def test_rotating_forwarded_for_shares_same_throttle_counter(self):
        """Confirm attempts with varying X-Forwarded-For accumulate on the same key."""
        limit = 2
        with override_settings(LOGIN_THROTTLE_MAX_ATTEMPTS=limit):
            self.client.post(
                "/login/",
                {"action": "login", "username": "throttle_student", "password": "wrong-password"},
                HTTP_X_FORWARDED_FOR="1.1.1.1",
            )
            self.client.post(
                "/login/",
                {"action": "login", "username": "throttle_student", "password": "wrong-password"},
                HTTP_X_FORWARDED_FOR="2.2.2.2",
            )
            # Third attempt should be blocked
            resp = self.client.post(
                "/login/",
                {"action": "login", "username": "throttle_student", "password": "wrong-password"},
                HTTP_X_FORWARDED_FOR="3.3.3.3",
            )
            self.assertEqual(resp.status_code, 429)

    def test_spoofed_forwarded_for_cannot_bypass_signup_throttle(self):
        """Rotating X-Forwarded-For header does not bypass signup throttling."""
        limit = 2
        with override_settings(LOGIN_THROTTLE_MAX_ATTEMPTS=limit):
            for i in range(limit):
                resp = self.client.post(
                    "/login/",
                    {
                        "action": "signup",
                        "username": "student_invalid",
                        "password1": "pass1",
                        "password2": "mismatch2",
                    },
                    HTTP_X_FORWARDED_FOR=f"10.200.0.{i + 1}",
                )
                self.assertEqual(resp.status_code, 200)

            blocked_resp = self.client.post(
                "/login/",
                {
                    "action": "signup",
                    "username": "student_invalid",
                    "password1": "pass1",
                    "password2": "mismatch2",
                },
                HTTP_X_FORWARDED_FOR="10.200.0.99",
            )
            self.assertEqual(blocked_resp.status_code, 429)

    def test_different_remote_addrs_independently_throttled(self):
        """Different physical REMOTE_ADDR clients do not exhaust each other's limit."""
        limit = 2
        with override_settings(LOGIN_THROTTLE_MAX_ATTEMPTS=limit):
            # Exhaust client A at 192.0.2.1
            for _ in range(limit):
                self.client.post(
                    "/login/",
                    {"action": "login", "username": "throttle_student", "password": "bad"},
                    REMOTE_ADDR="192.0.2.1",
                )
            self.assertEqual(
                self.client.post(
                    "/login/",
                    {"action": "login", "username": "throttle_student", "password": "bad"},
                    REMOTE_ADDR="192.0.2.1",
                ).status_code,
                429,
            )

            # Client B at 192.0.2.2 is unaffected
            client_b_resp = self.client.post(
                "/login/",
                {"action": "login", "username": "throttle_student", "password": "bad"},
                REMOTE_ADDR="192.0.2.2",
            )
            self.assertEqual(client_b_resp.status_code, 200)

    def test_ipv6_remote_addr_handled_consistently(self):
        """IPv6 REMOTE_ADDR is throttled properly without error."""
        limit = 2
        with override_settings(LOGIN_THROTTLE_MAX_ATTEMPTS=limit):
            for _ in range(limit):
                self.client.post(
                    "/login/",
                    {"action": "login", "username": "throttle_student", "password": "bad"},
                    REMOTE_ADDR="2001:db8::1",
                )
            resp = self.client.post(
                "/login/",
                {"action": "login", "username": "throttle_student", "password": "bad"},
                REMOTE_ADDR="2001:db8::1",
            )
            self.assertEqual(resp.status_code, 429)

    def test_forwarded_for_honored_only_when_proxy_trusted_and_addr_matches(self):
        """X-Forwarded-For is respected ONLY when TRUST_PROXY_HEADERS=True AND REMOTE_ADDR is in TRUSTED_PROXY_IPS."""
        rf = RequestFactory()

        # Case 1: Untrusted by default (TRUST_PROXY_HEADERS False)
        req1 = rf.get("/", HTTP_X_FORWARDED_FOR="203.0.113.195", REMOTE_ADDR="127.0.0.1")
        self.assertEqual(_client_ip(req1), "127.0.0.1")

        # Case 2: TRUST_PROXY_HEADERS True but REMOTE_ADDR is NOT in TRUSTED_PROXY_IPS
        with override_settings(TRUST_PROXY_HEADERS=True, TRUSTED_PROXY_IPS=["10.0.0.1"]):
            req2 = rf.get("/", HTTP_X_FORWARDED_FOR="203.0.113.195", REMOTE_ADDR="192.168.1.50")
            self.assertEqual(_client_ip(req2), "192.168.1.50")

        # Case 3: TRUST_PROXY_HEADERS True AND REMOTE_ADDR matches a trusted proxy
        with override_settings(TRUST_PROXY_HEADERS=True, TRUSTED_PROXY_IPS=["10.0.0.1"]):
            req3 = rf.get("/", HTTP_X_FORWARDED_FOR="203.0.113.195, 10.0.0.1", REMOTE_ADDR="10.0.0.1")
            self.assertEqual(_client_ip(req3), "203.0.113.195")