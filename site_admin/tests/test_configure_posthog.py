import io
import logging
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import requests

from site_admin.config import Settings
from site_admin.configure_posthog import (
    DESIRED_PROJECT_SETTINGS,
    PostHogConfigurationError,
    configure_project,
    main,
)


PROJECT_URL = (
    "https://us.posthog.com/api/organizations/"
    "00000000-0000-0000-0000-000000000000/projects/576032/"
)
EXPECTED_SETTINGS = {
    "timezone": "Asia/Shanghai",
    "anonymize_ips": False,
    "session_recording_opt_in": False,
    "capture_console_log_opt_in": False,
    "capture_performance_opt_in": False,
    "event_retention_months": 12,
    "app_urls": ["https://potatoma.me"],
}


class StubResponse:
    def __init__(self, *, status=200, payload=None, headers=None, json_error=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class ConfigurePostHogTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            admin_password="fixture-admin-password",
            session_secret="s" * 40,
            posthog_personal_key="fixture-personal-key",
            posthog_project_id=576032,
        )

    def test_check_gets_the_organization_project_and_reports_all_differences(self):
        current = {
            "timezone": "UTC",
            "anonymize_ips": True,
            "session_recording_opt_in": True,
            "capture_console_log_opt_in": True,
            "capture_performance_opt_in": True,
            "event_retention_months": 12,
            "app_urls": ["https://old.example"],
            "unrelated": "preserved",
        }

        with (
            patch(
                "requests.Session.get",
                return_value=StubResponse(payload=current),
            ) as get,
            patch("requests.Session.patch") as patch_request,
        ):
            changes = configure_project(self.settings)

        self.assertEqual(DESIRED_PROJECT_SETTINGS, EXPECTED_SETTINGS)
        self.assertEqual(
            changes,
            {
                name: (current[name], desired)
                for name, desired in EXPECTED_SETTINGS.items()
                if current[name] != desired
            },
        )
        get.assert_called_once_with(
            PROJECT_URL,
            headers={"Authorization": "Bearer fixture-personal-key"},
            timeout=10,
            allow_redirects=False,
        )
        patch_request.assert_not_called()

    def test_apply_patches_exact_settings_then_reads_back_all_seven_fields(self):
        before = dict(EXPECTED_SETTINGS, timezone="UTC")
        after = dict(EXPECTED_SETTINGS)

        with (
            patch(
                "requests.Session.get",
                side_effect=[
                    StubResponse(payload=before),
                    StubResponse(payload=after),
                ],
            ) as get,
            patch(
                "requests.Session.patch",
                return_value=StubResponse(status=200, payload={"ignored": True}),
            ) as patch_request,
        ):
            changes = configure_project(self.settings, apply=True)

        self.assertEqual(changes, {"timezone": ("UTC", "Asia/Shanghai")})
        self.assertEqual(get.call_count, 2)
        for call in get.call_args_list:
            self.assertEqual(
                call.args,
                (PROJECT_URL,),
            )
            self.assertEqual(
                call.kwargs,
                {
                    "headers": {"Authorization": "Bearer fixture-personal-key"},
                    "timeout": 10,
                    "allow_redirects": False,
                },
            )
        patch_request.assert_called_once_with(
            PROJECT_URL,
            headers={"Authorization": "Bearer fixture-personal-key"},
            json=EXPECTED_SETTINGS,
            timeout=10,
            allow_redirects=False,
        )

    def test_already_matching_runs_are_idempotent_and_never_patch(self):
        with (
            patch(
                "requests.Session.get",
                side_effect=[
                    StubResponse(payload=dict(EXPECTED_SETTINGS)),
                    StubResponse(payload=dict(EXPECTED_SETTINGS)),
                ],
            ) as get,
            patch("requests.Session.patch") as patch_request,
        ):
            first = configure_project(self.settings, apply=True)
            second = configure_project(self.settings, apply=True)

        self.assertEqual(first, {})
        self.assertEqual(second, {})
        self.assertEqual(get.call_count, 2)
        patch_request.assert_not_called()

    def test_boolean_fields_require_json_booleans_not_equal_integers(self):
        boolean_fields = (
            "anonymize_ips",
            "session_recording_opt_in",
            "capture_console_log_opt_in",
            "capture_performance_opt_in",
        )

        for field in boolean_fields:
            with self.subTest(field=field):
                current = dict(EXPECTED_SETTINGS, **{field: 0})
                with patch(
                    "requests.Session.get",
                    return_value=StubResponse(payload=current),
                ):
                    changes = configure_project(self.settings)

                self.assertEqual(changes, {field: (0, EXPECTED_SETTINGS[field])})

    def test_retention_requires_a_json_integer_not_an_equal_float(self):
        current = dict(EXPECTED_SETTINGS, event_retention_months=12.0)

        with patch(
            "requests.Session.get",
            return_value=StubResponse(payload=current),
        ):
            changes = configure_project(self.settings)

        self.assertEqual(changes, {"event_retention_months": (12.0, 12)})

    def test_app_urls_requires_a_json_list_with_exact_string_elements(self):
        malformed_values = (
            "https://potatoma.me",
            ("https://potatoma.me",),
            [576032],
        )

        for value in malformed_values:
            with self.subTest(value=value):
                current = dict(EXPECTED_SETTINGS, app_urls=value)
                with patch(
                    "requests.Session.get",
                    return_value=StubResponse(payload=current),
                ):
                    changes = configure_project(self.settings)

                self.assertEqual(
                    changes,
                    {"app_urls": (value, ["https://potatoma.me"])},
                )

    def test_apply_read_back_rejects_equal_values_with_wrong_json_types(self):
        malformed_read_backs = (
            dict(EXPECTED_SETTINGS, anonymize_ips=0),
            dict(EXPECTED_SETTINGS, event_retention_months=12.0),
            dict(EXPECTED_SETTINGS, app_urls=("https://potatoma.me",)),
            dict(EXPECTED_SETTINGS, app_urls=[576032]),
        )

        for read_back in malformed_read_backs:
            with self.subTest(read_back=read_back):
                before = dict(EXPECTED_SETTINGS, timezone="UTC")
                with (
                    patch(
                        "requests.Session.get",
                        side_effect=[
                            StubResponse(payload=before),
                            StubResponse(payload=read_back),
                        ],
                    ),
                    patch(
                        "requests.Session.patch",
                        return_value=StubResponse(payload={}),
                    ),
                ):
                    with self.assertRaises(PostHogConfigurationError):
                        configure_project(self.settings, apply=True)

    def test_apply_fails_safely_when_read_back_does_not_match(self):
        secret = "fixture-personal-key"
        unsafe_body = {"timezone": secret, "private": "raw upstream body"}
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("site_admin.configure_posthog")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        try:
            with (
                patch(
                    "requests.Session.get",
                    side_effect=[
                        StubResponse(payload=dict(EXPECTED_SETTINGS, timezone="UTC")),
                        StubResponse(payload=unsafe_body),
                    ],
                ),
                patch(
                    "requests.Session.patch",
                    return_value=StubResponse(payload={"token": secret}),
                ),
            ):
                with self.assertRaises(PostHogConfigurationError) as caught:
                    configure_project(self.settings, apply=True)
        finally:
            logger.removeHandler(handler)

        combined = stream.getvalue() + str(caught.exception)
        self.assertIn("project 576032", combined)
        self.assertNotIn(secret, combined)
        self.assertNotIn("raw upstream body", combined)
        self.assertNotIn("Authorization", combined)

    def test_http_failures_expose_only_safe_status_and_request_id(self):
        response = StubResponse(
            status=502,
            payload={"key": "fixture-personal-key", "body": "raw upstream body"},
            headers={
                "X-Request-ID": "request-502",
                "Location": "https://outside.example/fixture-personal-key",
            },
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("site_admin.configure_posthog")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        try:
            with patch("requests.Session.get", return_value=response):
                with self.assertRaises(PostHogConfigurationError) as caught:
                    configure_project(self.settings)
        finally:
            logger.removeHandler(handler)

        combined = stream.getvalue() + str(caught.exception)
        self.assertIn("status=502", stream.getvalue())
        self.assertIn("request_id=request-502", stream.getvalue())
        self.assertIn("project 576032", combined)
        for forbidden in (
            "fixture-personal-key",
            "raw upstream body",
            "outside.example",
            "Authorization",
        ):
            self.assertNotIn(forbidden, combined)

    def test_transport_error_does_not_echo_exception_or_environment_secrets(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("site_admin.configure_posthog")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        try:
            with patch(
                "requests.Session.get",
                side_effect=requests.Timeout(
                    "fixture-personal-key raw upstream body Authorization"
                ),
            ):
                with self.assertRaises(PostHogConfigurationError) as caught:
                    configure_project(self.settings)
        finally:
            logger.removeHandler(handler)

        combined = stream.getvalue() + str(caught.exception)
        self.assertIn("status=timeout", stream.getvalue())
        self.assertIn("project 576032", combined)
        for forbidden in (
            "fixture-personal-key",
            "raw upstream body",
            "Authorization",
        ):
            self.assertNotIn(forbidden, combined)

    def test_request_id_that_echoes_the_personal_key_is_redacted(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("site_admin.configure_posthog")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        try:
            with patch(
                "requests.Session.get",
                return_value=StubResponse(
                    status=503,
                    payload={},
                    headers={"X-Request-ID": "fixture-personal-key"},
                ),
            ):
                with self.assertRaises(PostHogConfigurationError):
                    configure_project(self.settings)
        finally:
            logger.removeHandler(handler)

        self.assertIn("request_id=unknown", stream.getvalue())
        self.assertNotIn("fixture-personal-key", stream.getvalue())

    def test_invalid_organization_id_is_rejected_before_network_without_echo(self):
        settings = Settings(
            admin_password="fixture-admin-password",
            session_secret="s" * 40,
            posthog_personal_key="fixture-personal-key",
            posthog_project_id=576032,
            posthog_organization_id="../fixture-personal-key",
        )

        with patch("requests.Session.get") as get:
            with self.assertRaises(PostHogConfigurationError) as caught:
                configure_project(settings)

        get.assert_not_called()
        self.assertIn("project 576032", str(caught.exception))
        self.assertNotIn("fixture-personal-key", str(caught.exception))

    def test_cli_defaults_to_check_and_only_apply_flag_requests_mutation(self):
        checks = [
            {"timezone": ("UTC", "Asia/Shanghai")},
            {"timezone": ("UTC", "Asia/Shanghai")},
        ]
        output = io.StringIO()
        errors = io.StringIO()

        with (
            patch(
                "site_admin.configure_posthog.Settings.from_env",
                return_value=self.settings,
            ),
            patch(
                "site_admin.configure_posthog.configure_project",
                side_effect=checks,
            ) as configure,
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            check_status = main([])
            apply_status = main(["--apply"])

        self.assertEqual(check_status, 0)
        self.assertEqual(apply_status, 0)
        self.assertEqual(
            [call.kwargs for call in configure.call_args_list],
            [{"apply": False}, {"apply": True}],
        )
        self.assertIn("project 576032", output.getvalue())
        self.assertIn("timezone", output.getvalue())
        self.assertIn("UTC -> Asia/Shanghai", output.getvalue())
        self.assertEqual(errors.getvalue(), "")
        for forbidden in (
            "fixture-personal-key",
            "Authorization",
            "SITE_ADMIN_PASSWORD",
            "SITE_ADMIN_SESSION_SECRET",
        ):
            self.assertNotIn(forbidden, output.getvalue())

    def test_documented_direct_script_entry_point_is_runnable(self):
        repository = Path(__file__).resolve().parents[2]

        result = subprocess.run(
            [
                sys.executable,
                str(repository / "site_admin" / "configure_posthog.py"),
                "--help",
            ],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--check", result.stdout)
        self.assertIn("--apply", result.stdout)


if __name__ == "__main__":
    unittest.main()
