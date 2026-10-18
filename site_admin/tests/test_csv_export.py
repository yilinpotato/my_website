import csv
import io
import re
import unittest
from urllib.parse import urlparse

from site_admin import create_app
from site_admin.config import Settings
from site_admin.csv_export import build_csv_export, sanitize_csv_cell
from site_admin.posthog_client import PostHogUnavailable, QueryResult


DETAIL_COLUMNS = (
    "timestamp",
    "uuid",
    "ip",
    "event",
    "path",
    "referrer",
    "device",
    "browser",
    "os",
    "country",
    "city",
    "comic_id",
)


def event_row(index, *, value="normal", timestamp="2026-08-25T07:06:05+00:00"):
    return (
        timestamp,
        f"00000000-0000-4000-8000-{index:012d}",
        value,
        value,
        value,
        value,
        value,
        value,
        value,
        value,
        value,
        value,
    )


class FakePostHogClient:
    def __init__(self, results):
        self._results = iter(results)
        self.calls = []

    def run_hogql(self, query, name, *, use_cache=True, timeout=10):
        self.calls.append((query, name, use_cache, timeout))
        result = next(self._results)
        if isinstance(result, Exception):
            raise result
        return result


class RepeatingPageClient:
    def __init__(self):
        self.calls = []

    def run_hogql(self, query, name, *, use_cache=True, timeout=10):
        page = len(self.calls)
        self.calls.append((query, name, use_cache, timeout))
        timestamp = f"2026-08-{25 - (page // 24):02d}T{23 - (page % 24):02d}:00:00+00:00"
        rows = tuple(event_row(index, timestamp=timestamp) for index in range(1000))
        return QueryResult(DETAIL_COLUMNS, rows)


class AdvancingClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class DeadlineClient:
    def __init__(self, clock, advances):
        self.clock = clock
        self.advances = iter(advances)
        self.calls = []

    def run_hogql(self, query, name, *, use_cache=True, timeout=10):
        page = len(self.calls)
        self.calls.append((query, name, use_cache, timeout))
        self.clock.now += next(self.advances)
        timestamp = f"2026-08-{25 - page:02d}T07:06:05+00:00"
        rows = tuple(event_row(index, timestamp=timestamp) for index in range(1000))
        return QueryResult(DETAIL_COLUMNS, rows)


class CsvCellTests(unittest.TestCase):
    def test_formula_leading_cells_are_prefixed_and_line_breaks_are_normalized(self):
        cases = {
            '=HYPERLINK("bad")': "'=HYPERLINK(\"bad\")",
            "+SUM(1,1)": "'+SUM(1,1)",
            "-2+3": "'-2+3",
            "@cmd": "'@cmd",
            "normal": "normal",
        }

        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(sanitize_csv_cell(source), expected)
        self.assertEqual(sanitize_csv_cell("line\r\nbreak\rnext\nlast"), "line break next last")

    def test_formula_detection_skips_leading_unicode_whitespace_and_controls(self):
        cases = {
            "\t=SUM(1,1)": "'\t=SUM(1,1)",
            "\f+SUM(1,1)": "'\f+SUM(1,1)",
            "\u00a0-2+3": "'\u00a0-2+3",
            "\u200b@cmd": "'\u200b@cmd",
            "\u2066=HYPERLINK(\"bad\")": "'\u2066=HYPERLINK(\"bad\")",
            "\x00@cmd": "'\x00@cmd",
            "\t normal": "\t normal",
        }

        for source, expected in cases.items():
            with self.subTest(source=repr(source)):
                self.assertEqual(sanitize_csv_cell(source), expected)

class CsvExportRouteTests(unittest.TestCase):
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
        self.client.post(
            "/login",
            data={"password": "fixture-admin-password"},
        )

    def install_fake(self, fake):
        self.app.extensions["site_admin_posthog_client"] = fake

    def test_export_uses_validated_filters_keyset_pagination_and_safe_cells(self):
        first_rows = tuple(event_row(index) for index in range(999)) + (
            event_row(999, value='=HYPERLINK("bad")'),
        )
        second_rows = (
            event_row(1000, value="+SUM(1,1)", timestamp="2026-08-24T07:06:05+00:00"),
            event_row(1001, value="-2+3", timestamp="2026-08-24T07:06:04+00:00"),
            event_row(1002, value="@cmd", timestamp="2026-08-24T07:06:03+00:00"),
        )
        fake = FakePostHogClient(
            (
                QueryResult(DETAIL_COLUMNS, first_rows),
                QueryResult(DETAIL_COLUMNS, second_rows),
            )
        )
        self.install_fake(fake)

        response = self.client.get(
            "/export.csv",
            query_string={
                "days": "7",
                "ip": "2001:0db8::0001",
                "path": "/jm/view/123?mode=grid",
                "event": "comic_opened",
                "comic_id": "123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertEqual(response.mimetype, "text/csv")
        disposition = response.headers["Content-Disposition"]
        self.assertRegex(
            disposition,
            r'^attachment; filename=site-admin-events-\d{8}T\d{6}Z\.csv$',
        )
        self.assertTrue(response.data.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
        self.assertEqual(rows[0], list(DETAIL_COLUMNS))
        self.assertEqual(len(rows), 1004)
        self.assertEqual(rows[1000][2], "'=HYPERLINK(\"bad\")")
        self.assertEqual(rows[1001][2], "'+SUM(1,1)")
        self.assertEqual(rows[1002][2], "'-2+3")
        self.assertEqual(rows[1003][2], "'@cmd")

        self.assertEqual(len(fake.calls), 2)
        for query, name, use_cache, timeout in fake.calls:
            self.assertEqual(name, "site-admin-export-events")
            self.assertFalse(use_cache)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 10)
            self.assertIn("INTERVAL 7 DAY", query)
            self.assertIn("toString(properties.$ip) = '2001:db8::1'", query)
            self.assertIn("toString(properties.path) = '/jm/view/123?mode=grid'", query)
            self.assertIn("event = 'comic_opened'", query)
            self.assertIn("toString(properties.comic_id) = '123'", query)
            self.assertIn("ORDER BY timestamp DESC, uuid DESC", query)
            self.assertIn("LIMIT 1000", query)
            self.assertNotIn("OFFSET", query.upper())
        self.assertNotIn("timestamp <", fake.calls[0][0])
        self.assertIn("timestamp <", fake.calls[1][0])
        self.assertIn("00000000-0000-4000-8000-000000000999", fake.calls[1][0])

    def test_export_stops_at_ten_thousand_rows(self):
        fake = RepeatingPageClient()
        self.install_fake(fake)

        response = self.client.get("/export.csv", query_string={"days": "90"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(fake.calls), 10)
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 10001)
        self.assertTrue(all(call[2] is False for call in fake.calls))
        self.assertTrue(all(0 < call[3] <= 10 for call in fake.calls))
        self.assertTrue(all("OFFSET" not in call[0].upper() for call in fake.calls))

    def test_export_deadline_caps_each_request_and_fails_after_twenty_five_seconds(self):
        clock = AdvancingClock()
        fake = DeadlineClient(clock, (9.0, 9.0, 8.0))

        with self.assertRaises(PostHogUnavailable):
            build_csv_export(fake, filters=self._default_filters(), clock=clock)

        self.assertEqual(len(fake.calls), 3)
        self.assertEqual([call[3] for call in fake.calls], [10.0, 10.0, 7.0])

    def test_invalid_filter_returns_safe_400_without_querying_posthog(self):
        fake = FakePostHogClient(())
        self.install_fake(fake)

        response = self.client.get("/export.csv", query_string={"days": "91"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["field"], "days")
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertEqual(fake.calls, [])

    def test_bad_later_pages_return_only_the_safe_503_boundary(self):
        failures = (
            QueryResult(DETAIL_COLUMNS, tuple(event_row(index) for index in range(1001))),
            QueryResult(("timestamp", "uuid"), (event_row(0),)),
            PostHogUnavailable("fixture-personal-key SELECT private"),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                first_page = QueryResult(
                    DETAIL_COLUMNS,
                    tuple(event_row(index) for index in range(1000)),
                )
                fake = FakePostHogClient((first_page, failure))
                self.install_fake(fake)

                response = self.client.get("/export.csv")

                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.get_json(),
                    {"error": "PostHog 暂时不可用，请稍后重试。"},
                )
                self.assertEqual(response.headers.get("Cache-Control"), "no-store")
                body = response.get_data(as_text=True)
                self.assertNotIn("fixture-personal-key", body)
                self.assertNotIn("SELECT", body.upper())
                self.assertFalse(response.data.startswith(b"\xef\xbb\xbf"))

    def test_cell_larger_than_four_kib_returns_only_the_safe_503_boundary(self):
        oversized = "界" * 1366
        fake = FakePostHogClient(
            (QueryResult(DETAIL_COLUMNS, (event_row(0, value=oversized),)),)
        )
        self.install_fake(fake)

        response = self.client.get("/export.csv")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {"error": "PostHog 暂时不可用，请稍后重试。"},
        )
        self.assertFalse(response.data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn("界", response.get_data(as_text=True))

    def test_completed_export_larger_than_sixteen_mib_is_rejected(self):
        large_cell = "x" * 4096

        class LargeExportClient:
            def __init__(self):
                self.calls = 0

            def run_hogql(self, query, name, *, use_cache=True, timeout=10):
                page = self.calls
                self.calls += 1
                timestamp = f"2026-08-{25 - page:02d}T07:06:05+00:00"
                rows = tuple(
                    (
                        timestamp,
                        f"00000000-0000-4000-8000-{index:012d}",
                        large_cell,
                        *("x" for _ in range(9)),
                    )
                    for index in range(1000)
                )
                return QueryResult(DETAIL_COLUMNS, rows)

        fake = LargeExportClient()
        self.install_fake(fake)

        response = self.client.get("/export.csv")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {"error": "PostHog 暂时不可用，请稍后重试。"},
        )
        self.assertGreaterEqual(fake.calls, 4)
        self.assertLessEqual(fake.calls, 5)
        self.assertFalse(response.data.startswith(b"\xef\xbb\xbf"))

    @staticmethod
    def _default_filters():
        from site_admin.queries import AnalyticsFilters

        return AnalyticsFilters()


if __name__ == "__main__":
    unittest.main()
