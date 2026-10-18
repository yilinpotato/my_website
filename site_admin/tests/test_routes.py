import unittest
from html.parser import HTMLParser
from urllib.parse import urlparse

from site_admin import create_app
from site_admin.config import Settings


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.kpis = []
        self.rankings = []
        self.inputs = set()
        self.ids = set()
        self.forms = []
        self.links = []
        self.scripts = []
        self.svg_ids = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.ids.add(element_id)
        if "data-kpi" in attributes:
            self.kpis.append(attributes["data-kpi"])
        if "data-ranking-kind" in attributes:
            self.rankings.append(attributes["data-ranking-kind"])
        if tag in {"input", "select"} and attributes.get("name"):
            self.inputs.add(attributes["name"])
        if tag == "form":
            self.forms.append(attributes)
        if tag == "a":
            self.links.append(attributes)
        if tag == "script":
            self.scripts.append(attributes)
        if tag == "svg" and element_id:
            self.svg_ids.add(element_id)


class DashboardRouteTests(unittest.TestCase):
    def setUp(self):
        settings = Settings(
            admin_password="fixture-admin-password",
            session_secret="s" * 40,
            posthog_personal_key="fixture-personal-key",
            posthog_project_id=576032,
        )
        self.app = create_app(settings)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def login(self):
        return self.client.post(
            "/login",
            data={"password": "fixture-admin-password"},
        )

    def test_authenticated_dashboard_renders_the_complete_standalone_shell(self):
        self.login()

        response = self.client.get("/")
        parser = DashboardParser()
        parser.feed(response.get_data(as_text=True))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        html = response.get_data(as_text=True)
        self.assertIn("站点访问记录", html)
        self.assertIn("IP 留空时显示全部 IP 的操作", html)
        self.assertCountEqual(
            parser.kpis,
            (
                "pageviews",
                "unique_visitors",
                "unique_ips",
                "primary_clicks",
                "comic_entries",
                "unique_comic_ids",
            ),
        )
        self.assertCountEqual(
            parser.rankings,
            ("page", "referrer", "country", "device", "operation", "comic"),
        )
        self.assertEqual(
            parser.inputs,
            {"days", "ip", "path", "event", "comic_id"},
        )
        self.assertIn("trend-chart", parser.svg_ids)
        for element_id in (
            "event-table-body",
            "retry-button",
            "last-updated",
            "stale-indicator",
            "previous-page",
            "next-page",
        ):
            self.assertIn(element_id, parser.ids)

        export_links = [link for link in parser.links if link.get("id") == "export-link"]
        self.assertEqual(len(export_links), 1)
        self.assertEqual(urlparse(export_links[0]["href"]).path, "/site-admin/export.csv")
        logout_forms = [form for form in parser.forms if form.get("id") == "logout-form"]
        self.assertEqual(len(logout_forms), 1)
        self.assertEqual(logout_forms[0].get("method"), "post")
        self.assertEqual(urlparse(logout_forms[0]["action"]).path, "/site-admin/logout")
        self.assertEqual(
            [urlparse(script["src"]).path for script in parser.scripts if "src" in script],
            ["/site-admin/static/admin.js"],
        )

    def test_dashboard_and_export_require_authentication_and_disable_storage(self):
        dashboard = self.client.get("/")
        export = self.client.get("/export.csv")

        for response in (dashboard, export):
            with self.subTest(location=response.location):
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    urlparse(response.location).path,
                    "/site-admin/login",
                )
                self.assertEqual(response.headers.get("Cache-Control"), "no-store")


if __name__ == "__main__":
    unittest.main()
