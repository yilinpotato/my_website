import io
import logging
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from unittest.mock import patch
from urllib.parse import urlparse

from site_admin import create_app
from site_admin.auth import LoginLimiter
from site_admin.config import Settings


class LoginLimiterTests(unittest.TestCase):
    def test_fifth_failure_establishes_a_fifteen_minute_lock(self):
        limiter = LoginLimiter()
        ip = "203.0.113.5"

        for _ in range(5):
            allowed, remaining = limiter.allow(ip, 100.0)
            self.assertTrue(allowed)
            self.assertEqual(remaining, 0)
            limiter.record_failure(ip, 100.0)

        allowed, remaining = limiter.allow(ip, 100.0)
        self.assertFalse(allowed)
        self.assertEqual(remaining, 900)

    def test_failures_expire_after_fifteen_minutes(self):
        limiter = LoginLimiter()
        ip = "203.0.113.5"
        for _ in range(5):
            limiter.record_failure(ip, 100.0)

        allowed, remaining = limiter.allow(ip, 1000.0)

        self.assertTrue(allowed)
        self.assertEqual(remaining, 0)

    def test_access_from_another_ip_prunes_all_expired_entries(self):
        limiter = LoginLimiter()
        for index in range(2_000):
            limiter.record_failure(f"stale-{index}", 100.0)

        limiter.allow("203.0.113.5", 1000.0)

        self.assertEqual(limiter._failures, {})

    def test_capacity_evicts_oldest_unlocked_ip_without_weakening_active_lock(self):
        limiter = LoginLimiter()
        locked_ip = "203.0.113.5"
        for _ in range(5):
            limiter.record_failure(locked_ip, 100.0)
        limiter.record_failure("oldest-unlocked", 100.5)
        for index in range(1_022):
            limiter.record_failure(f"unlocked-{index}", 101.0)

        limiter.record_failure("newest", 102.0)

        self.assertLessEqual(len(limiter._failures), 1_024)
        self.assertNotIn("oldest-unlocked", limiter._failures)
        allowed, remaining = limiter.allow(locked_ip, 102.0)
        self.assertFalse(allowed)
        self.assertEqual(remaining, 898)

    def test_concurrent_failures_cannot_exceed_capacity_or_remove_active_lock(self):
        limiter = LoginLimiter()
        locked_ip = "203.0.113.5"
        for _ in range(5):
            limiter.record_failure(locked_ip, 100.0)

        with ThreadPoolExecutor(max_workers=16) as executor:
            list(
                executor.map(
                    lambda index: limiter.record_failure(f"parallel-{index}", 101.0),
                    range(2_000),
                )
            )

        self.assertLessEqual(len(limiter._failures), 1_024)
        allowed, remaining = limiter.allow(locked_ip, 102.0)
        self.assertFalse(allowed)
        self.assertEqual(remaining, 898)


class AdministratorAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            admin_password="fixture-admin-password",
            session_secret="s" * 40,
            posthog_personal_key="fixture-personal-key",
            posthog_project_id=576032,
        )
        self.app = create_app(self.settings)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

        self.log_stream = io.StringIO()
        self.log_handler = logging.StreamHandler(self.log_stream)
        self.security_logger = logging.getLogger("site_admin.security")
        self.security_logger.addHandler(self.log_handler)
        self.security_logger.setLevel(logging.INFO)

    def tearDown(self):
        self.security_logger.removeHandler(self.log_handler)

    def login(self, password="fixture-admin-password", **kwargs):
        return self.client.post("/login", data={"password": password}, **kwargs)

    def test_unauthenticated_dashboard_redirects_to_login(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.location).path, "/site-admin/login")

    def test_login_page_has_one_password_field_alert_region_and_home_link(self):
        response = self.client.get("/login")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(re.findall(r'<input[^>]+type="password"', html)), 1)
        self.assertIn('role="alert"', html)
        self.assertIn('href="/"', html)
        self.assertNotIn("seven", html.lower())
        self.assertNotIn("click gesture", html.lower())

    def test_correct_password_authenticates_with_a_permanent_session(self):
        response = self.login()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.location).path, "/site-admin/")
        with self.client.session_transaction() as session:
            self.assertTrue(session["site_admin_authenticated"])
            self.assertTrue(session.permanent)

    def test_cookie_has_required_security_attributes_and_admin_path(self):
        response = self.login()
        cookie = response.headers["Set-Cookie"]

        self.assertIn("site_admin_session=", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/site-admin", cookie)

    def test_authenticated_activity_refreshes_the_two_hour_idle_window(self):
        login_response = self.login()
        activity_response = self.client.get("/")

        for response in (login_response, activity_response):
            cookie = response.headers["Set-Cookie"]
            expires = cookie.split("Expires=", 1)[1].split(";", 1)[0]
            remaining = (
                parsedate_to_datetime(expires) - datetime.now(timezone.utc)
            ).total_seconds()
            self.assertGreaterEqual(remaining, 7195)
            self.assertLessEqual(remaining, 7200)
        self.assertTrue(self.app.config["SESSION_REFRESH_EACH_REQUEST"])

    def test_logout_clears_authentication(self):
        self.login()

        response = self.client.post("/logout")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.location).path, "/site-admin/login")
        with self.client.session_transaction() as session:
            self.assertNotIn("site_admin_authenticated", session)

    def test_password_check_uses_constant_time_comparison(self):
        with patch("site_admin.auth.hmac.compare_digest", return_value=True) as compare:
            response = self.login("candidate-value")

        self.assertEqual(response.status_code, 302)
        compare.assert_called_once_with(
            b"candidate-value",
            b"fixture-admin-password",
        )

    def test_non_ascii_wrong_password_is_an_authentication_failure(self):
        response = self.login("not-the-password-\u5bc6\u7801")

        self.assertEqual(response.status_code, 401)

    def test_fifth_failure_locks_ip_for_fifteen_minutes(self):
        for _ in range(5):
            response = self.login(
                "wrong",
                environ_base={"REMOTE_ADDR": "203.0.113.5"},
            )
            self.assertEqual(response.status_code, 401)

        response = self.login(
            "wrong",
            environ_base={"REMOTE_ADDR": "203.0.113.5"},
        )

        self.assertEqual(response.status_code, 429)
        match = re.search(r"try again in (\d+) seconds", response.get_data(as_text=True).lower())
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 1)
        self.assertLessEqual(int(match.group(1)), 900)

    def test_success_clears_previous_failures(self):
        address = {"REMOTE_ADDR": "203.0.113.5"}
        for _ in range(4):
            self.login("wrong", environ_base=address)

        self.login(environ_base=address)
        self.client.post("/logout", environ_base=address)
        for _ in range(4):
            response = self.login("wrong", environ_base=address)
            self.assertEqual(response.status_code, 401)

        response = self.login(environ_base=address)
        self.assertEqual(response.status_code, 302)

    def test_proxyfix_trusts_only_the_rightmost_forwarded_client_ip(self):
        headers = {"X-Forwarded-For": "198.51.100.99, 203.0.113.5"}
        for _ in range(5):
            self.login("wrong", headers=headers, environ_base={"REMOTE_ADDR": "10.0.0.2"})

        changed_leftmost = {"X-Forwarded-For": "192.0.2.77, 203.0.113.5"}
        response = self.login(
            "wrong",
            headers=changed_leftmost,
            environ_base={"REMOTE_ADDR": "10.0.0.2"},
        )

        self.assertEqual(response.status_code, 429)
        self.assertIn("203.0.113.5", self.log_stream.getvalue())
        self.assertNotIn("192.0.2.77", self.log_stream.getvalue())

    def test_forwarded_prefix_generates_the_external_admin_url(self):
        response = self.client.get(
            "/",
            headers={"X-Forwarded-Prefix": "/site-admin"},
        )

        self.assertEqual(urlparse(response.location).path, "/site-admin/login")

    def test_security_logs_are_minimal_for_all_authentication_events(self):
        address = {"REMOTE_ADDR": "203.0.113.5"}
        self.login("submitted-wrong-value", environ_base=address)
        self.login(environ_base=address)
        cookie = self.client.get_cookie(
            "site_admin_session",
            path="/site-admin",
        ).value
        self.client.post("/logout", data={"unexpected": "request-body-value"}, environ_base=address)
        for _ in range(5):
            self.login("wrong", environ_base=address)
        self.login("wrong", environ_base=address)
        lines = [line for line in self.log_stream.getvalue().splitlines() if line]

        self.assertTrue(any("login_failure" in line for line in lines))
        self.assertTrue(any("login_success" in line for line in lines))
        self.assertTrue(any("logout" in line for line in lines))
        self.assertTrue(any("rate_limit" in line for line in lines))
        self.assertTrue(all("203.0.113.5" in line for line in lines))
        self.assertTrue(all("timestamp=" in line for line in lines))
        forbidden = (
            "submitted-wrong-value",
            "fixture-admin-password",
            "fixture-personal-key",
            "request-body-value",
            cookie,
            "s" * 40,
        )
        for value in forbidden:
            self.assertNotIn(value, self.log_stream.getvalue())
        for line in lines:
            self.assertRegex(
                line,
                r"^event=(login_failure|login_success|logout|rate_limit) "
                r"ip=203\.0\.113\.5 timestamp=\d{4}-\d{2}-\d{2}T"
                r"\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$",
            )

    def test_every_admin_response_disables_storage(self):
        responses = (
            self.client.get("/login"),
            self.client.get("/"),
            self.login("wrong"),
            self.client.get("/missing"),
        )

        for response in responses:
            with self.subTest(status=response.status_code):
                self.assertEqual(response.headers.get("Cache-Control"), "no-store")


if __name__ == "__main__":
    unittest.main()
