import unittest
from dataclasses import FrozenInstanceError

from site_admin.config import Settings


class SettingsTests(unittest.TestCase):
    def valid_mapping(self):
        return {
            "SITE_ADMIN_PASSWORD": "fixture-admin-password",
            "SITE_ADMIN_SESSION_SECRET": "s" * 40,
            "POSTHOG_PERSONAL_API_KEY": "fixture-personal-key",
            "POSTHOG_PROJECT_ID": "576032",
        }

    def test_missing_secret_names_fail_without_leaking_values(self):
        with self.assertRaisesRegex(RuntimeError, "SITE_ADMIN_SESSION_SECRET") as caught:
            Settings.from_mapping({"SITE_ADMIN_PASSWORD": "configured"})

        self.assertNotIn("configured", str(caught.exception))

    def test_empty_password_is_rejected(self):
        mapping = self.valid_mapping()
        mapping["SITE_ADMIN_PASSWORD"] = "   "

        with self.assertRaisesRegex(RuntimeError, "SITE_ADMIN_PASSWORD"):
            Settings.from_mapping(mapping)

    def test_short_session_secret_is_rejected(self):
        mapping = self.valid_mapping()
        mapping["SITE_ADMIN_SESSION_SECRET"] = "too-short"

        with self.assertRaisesRegex(RuntimeError, "at least 32 characters"):
            Settings.from_mapping(mapping)

    def test_nonnumeric_project_id_is_rejected(self):
        mapping = self.valid_mapping()
        mapping["POSTHOG_PROJECT_ID"] = "not-a-number"

        with self.assertRaisesRegex(RuntimeError, "must be numeric"):
            Settings.from_mapping(mapping)

    def test_non_https_posthog_host_is_rejected(self):
        mapping = self.valid_mapping()
        mapping["POSTHOG_API_HOST"] = "http://posthog.example.test"

        with self.assertRaisesRegex(RuntimeError, "must use HTTPS"):
            Settings.from_mapping(mapping)

    def test_posthog_host_rejects_credentials_ports_paths_queries_fragments_and_other_origins(self):
        invalid_hosts = (
            "https://eu.posthog.com",
            "https://us.i.posthog.com",
            "https://us.posthog.com.evil.example",
            "https://user@us.posthog.com",
            "https://user:password@us.posthog.com",
            "https://us.posthog.com:443",
            "https://us.posthog.com/",
            "https://us.posthog.com/api",
            "https://us.posthog.com?query=1",
            "https://us.posthog.com#fragment",
        )

        for host in invalid_hosts:
            with self.subTest(host=host):
                mapping = self.valid_mapping()
                mapping["POSTHOG_API_HOST"] = host
                with self.assertRaisesRegex(RuntimeError, "exactly https://us.posthog.com"):
                    Settings.from_mapping(mapping)

    def test_valid_mapping_is_normalized_and_immutable(self):
        mapping = self.valid_mapping()
        mapping.update(
            {
                "POSTHOG_API_HOST": "HTTPS://US.POSTHOG.COM",
                "POSTHOG_ORGANIZATION_ID": "fixture-organization",
            }
        )

        settings = Settings.from_mapping(mapping)

        self.assertEqual(settings.admin_password, "fixture-admin-password")
        self.assertEqual(settings.posthog_project_id, 576032)
        self.assertEqual(settings.posthog_api_host, "https://us.posthog.com")
        self.assertEqual(settings.posthog_organization_id, "fixture-organization")
        self.assertEqual(settings.url_prefix, "/site-admin")
        with self.assertRaises(FrozenInstanceError):
            settings.url_prefix = "/changed"


if __name__ == "__main__":
    unittest.main()
