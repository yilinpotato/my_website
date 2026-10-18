import base64
import json
import re
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import patch

import requests

from site_admin import create_app
from site_admin.config import Settings
from site_admin.queries import (
    APPROVED_EVENTS,
    AnalyticsFilters,
    EventCursor,
    InvalidFilter,
    detail_query,
    export_detail_query,
    quote_hogql_string,
    ranking_query,
    summary_query,
    trend_query,
)


def encode_cursor_fixture(timestamp: str) -> str:
    payload = json.dumps(
        [timestamp, "12345678-1234-4abc-8def-1234567890ab"],
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


class StubResponse:
    def __init__(self, *, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class AnalyticsFilterTests(unittest.TestCase):
    def test_days_default_to_thirty_and_accept_only_the_four_fixed_windows(self):
        self.assertEqual(AnalyticsFilters.from_request({}).days, 30)

        for value, expected in (("1", 1), ("7", 7), ("30", 30), ("90", 90)):
            with self.subTest(value=value):
                self.assertEqual(
                    AnalyticsFilters.from_request({"days": value}).days,
                    expected,
                )

        for value in ("", "0", "01", "2", "29", "31", "91", "-1", "30.0", "x"):
            with self.subTest(value=value):
                with self.assertRaises(InvalidFilter) as caught:
                    AnalyticsFilters.from_request({"days": value})
                self.assertEqual(caught.exception.field, "days")

        for value in (30.0, "30", True):
            with self.subTest(direct_value=value):
                with self.assertRaises(InvalidFilter) as caught:
                    AnalyticsFilters(days=value)
                self.assertEqual(caught.exception.field, "days")

    def test_event_accepts_exactly_the_seven_approved_names(self):
        self.assertEqual(
            APPROVED_EVENTS,
            frozenset(
                {
                    "$pageview",
                    "primary_click",
                    "language_changed",
                    "theme_changed",
                    "comic_viewer_entered",
                    "comic_opened",
                    "training_started",
                }
            ),
        )
        for event in APPROVED_EVENTS:
            with self.subTest(event=event):
                self.assertEqual(
                    AnalyticsFilters.from_request({"event": event}).event,
                    event,
                )
        for event in ("pageview", "custom", "$pageview' OR 1=1 --"):
            with self.subTest(event=event):
                with self.assertRaises(InvalidFilter) as caught:
                    AnalyticsFilters.from_request({"event": event})
                self.assertEqual(caught.exception.field, "event")

    def test_ip_is_validated_and_canonicalized_by_ipaddress(self):
        self.assertEqual(
            AnalyticsFilters.from_request({"ip": "203.0.113.8"}).ip,
            "203.0.113.8",
        )
        self.assertEqual(
            AnalyticsFilters.from_request({"ip": "2001:0db8::0001"}).ip,
            "2001:db8::1",
        )
        for value in ("999.0.0.1", "203.0.113.8' OR 1=1", "not-an-ip"):
            with self.subTest(value=value):
                with self.assertRaises(InvalidFilter) as caught:
                    AnalyticsFilters.from_request({"ip": value})
                self.assertEqual(caught.exception.field, "ip")

    def test_ip_rejects_ipv6_scopes_and_non_address_format_characters(self):
        hostile_values = (
            "fe80::1%eth0",
            "fe80::1%scope\nbad",
            "fe80::1%scope;DROP TABLE events;--",
            "fe80::1%25eth0",
            "fe80::1‮reversal",
        )

        for value in hostile_values:
            with self.subTest(value=repr(value)):
                with self.assertRaises(InvalidFilter) as caught:
                    AnalyticsFilters.from_request({"ip": value})
                self.assertEqual(caught.exception.field, "ip")

    def test_comic_id_is_one_to_sixteen_ascii_digits(self):
        for value in ("0", "7", "1234567890123456"):
            with self.subTest(value=value):
                self.assertEqual(
                    AnalyticsFilters.from_request({"comic_id": value}).comic_id,
                    value,
                )
        for value in ("", "-1", "1.0", "12345678901234567", "１", "1 OR 1=1"):
            with self.subTest(value=value):
                args = {} if value == "" else {"comic_id": value}
                if not args:
                    self.assertIsNone(AnalyticsFilters.from_request(args).comic_id)
                    continue
                with self.assertRaises(InvalidFilter) as caught:
                    AnalyticsFilters.from_request(args)
                self.assertEqual(caught.exception.field, "comic_id")

    def test_path_validation_blocks_every_query_structure_character(self):
        valid = "/jm/view/123?mode=grid&lang=zh-CN"
        self.assertEqual(AnalyticsFilters.from_request({"path": valid}).path, valid)
        self.assertEqual(
            AnalyticsFilters.from_request({"path": "/" + "a" * 199}).path,
            "/" + "a" * 199,
        )

        invalid = (
            "relative/path",
            "/" + "a" * 200,
            "/quote'",
            '/double"quote',
            "/back\\slash",
            "/line\nbreak",
            "/tab\tpath",
            "/line separator",
            "/bidi‮reversal",
            "/semi;colon",
            "/dash--comment",
            "/slash/*comment",
            "/close*/comment",
            "/hash#comment",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(InvalidFilter) as caught:
                    AnalyticsFilters.from_request({"path": value})
                self.assertEqual(caught.exception.field, "path")

    def test_validated_filters_are_immutable(self):
        filters = AnalyticsFilters.from_request({})

        with self.assertRaises(FrozenInstanceError):
            filters.days = 7


class HogQLBuilderTests(unittest.TestCase):
    def test_every_report_has_the_fixed_seven_event_whitelist_without_filters(self):
        whitelist = (
            "event IN ('$pageview', 'primary_click', 'language_changed', "
            "'theme_changed', 'comic_viewer_entered', 'comic_opened', "
            "'training_started')"
        )
        specs = (
            summary_query(AnalyticsFilters()),
            trend_query(AnalyticsFilters()),
            *(ranking_query(kind, AnalyticsFilters()) for kind in (
                "page", "referrer", "country", "device", "operation", "comic"
            )),
            detail_query(AnalyticsFilters(), None),
            export_detail_query(AnalyticsFilters(), None),
        )

        for spec in specs:
            with self.subTest(name=spec.name):
                self.assertIn(whitelist, spec.query)

    def test_quoting_helper_escapes_quotes_and_backslashes_as_one_literal(self):
        hostile = "value'; DROP TABLE events;--\\tail/*comment*/"

        quoted = quote_hogql_string(hostile)

        self.assertEqual(
            quoted,
            "'value\\'; DROP TABLE events;--\\\\tail/*comment*/'",
        )
        self.assertTrue(quoted.startswith("'"))
        self.assertTrue(quoted.endswith("'"))
        self.assertNotIn("value';", quoted)

    def test_quoting_helper_rejects_ascii_and_unicode_control_characters(self):
        for value in (
            "line\nbreak",
            "tab\tvalue",
            "nul\0value",
            "c1\x85value",
            "line separator",
            "bidi‮reversal",
        ):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    quote_hogql_string(value)

    def test_every_report_has_requested_and_independent_hard_window_bounds(self):
        builders = (
            lambda filters: summary_query(filters),
            lambda filters: trend_query(filters),
            lambda filters: ranking_query("page", filters),
            lambda filters: detail_query(filters, None),
        )
        for days in (1, 7, 30, 90):
            for build in builders:
                with self.subTest(days=days, builder=build):
                    query = build(AnalyticsFilters(days=days)).query
                    self.assertIn(
                        f"timestamp >= now() - INTERVAL {days} DAY",
                        query,
                    )
                    self.assertIn(
                        "timestamp >= now() - INTERVAL 90 DAY",
                        query,
                    )
                    if days == 90:
                        self.assertGreaterEqual(
                            query.count("timestamp >= now() - INTERVAL 90 DAY"),
                            2,
                        )

    def test_all_request_filters_enter_queries_only_as_single_literals(self):
        filters = AnalyticsFilters.from_request(
            {
                "days": "7",
                "ip": "2001:0db8::0001",
                "path": "/jm/view/123?mode=grid",
                "event": "comic_opened",
                "comic_id": "123",
            }
        )

        for spec in (
            summary_query(filters),
            trend_query(filters),
            ranking_query("comic", filters),
            detail_query(filters, None),
            export_detail_query(filters, None),
        ):
            with self.subTest(name=spec.name):
                self.assertIn("toString(properties.$ip) = '2001:db8::1'", spec.query)
                self.assertIn("toString(properties.path) = '/jm/view/123?mode=grid'", spec.query)
                self.assertIn("event = 'comic_opened'", spec.query)
                self.assertIn("toString(properties.comic_id) = '123'", spec.query)

    def test_reports_read_only_events_and_the_approved_property_set(self):
        allowed_properties = {
            "properties.$ip",
            "properties.$device_type",
            "properties.$browser",
            "properties.$os",
            "properties.$geoip_country_name",
            "properties.$geoip_city_name",
            "properties.path",
            "properties.referrer",
            "properties.control_id",
            "properties.label",
            "properties.comic_id",
        }
        specs = (
            summary_query(AnalyticsFilters()),
            trend_query(AnalyticsFilters()),
            *(ranking_query(kind, AnalyticsFilters()) for kind in (
                "page", "referrer", "country", "device", "operation", "comic"
            )),
            detail_query(AnalyticsFilters(), None),
            export_detail_query(AnalyticsFilters(), None),
        )

        for spec in specs:
            with self.subTest(name=spec.name):
                self.assertEqual(re.findall(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)", spec.query), ["events"])
                referenced = set(
                    re.findall(r"properties\.\$?[A-Za-z_][A-Za-z0-9_]*", spec.query)
                )
                self.assertLessEqual(referenced, allowed_properties)

    def test_summary_calculates_every_fixed_metric(self):
        spec = summary_query(AnalyticsFilters())

        self.assertTrue(spec.use_cache)
        for alias in (
            "total_events",
            "pageviews",
            "unique_visitors",
            "unique_ips",
            "primary_clicks",
            "comic_entries",
            "unique_comic_ids",
        ):
            self.assertRegex(spec.query, rf"\bAS\s+{alias}\b")
        for event in ("$pageview", "primary_click", "comic_viewer_entered", "comic_opened"):
            self.assertIn(quote_hogql_string(event), spec.query)
        self.assertIn(
            "uniqExactIf(toString(properties.$ip), "
            "notEmpty(toString(properties.$ip))) AS unique_ips",
            spec.query.replace("\n", " "),
        )

    def test_trend_groups_pageviews_by_shanghai_calendar_day(self):
        spec = trend_query(AnalyticsFilters())

        self.assertTrue(spec.use_cache)
        self.assertIn("toDate(timestamp) AS day", spec.query)
        self.assertIn("event = '$pageview'", spec.query)
        self.assertIn("GROUP BY day", spec.query)
        self.assertIn("ORDER BY day ASC", spec.query)

    def test_ranking_kind_is_an_exact_six_value_allowlist(self):
        for kind in ("page", "referrer", "country", "device", "operation", "comic"):
            with self.subTest(kind=kind):
                spec = ranking_query(kind, AnalyticsFilters())
                self.assertEqual(spec.name, f"site-admin-ranking-{kind}")
                self.assertTrue(spec.use_cache)
                self.assertIn("LIMIT 20", spec.query)

        for kind in ("", "browser", "page; DROP TABLE events", "PAGE"):
            with self.subTest(kind=kind):
                with self.assertRaises(InvalidFilter) as caught:
                    ranking_query(kind, AnalyticsFilters())
                self.assertEqual(caught.exception.field, "kind")

    def test_cursor_round_trip_is_url_safe_and_decode_is_strict(self):
        cursor = EventCursor(
            datetime(2026, 8, 25, 7, 6, 5, 123456, tzinfo=timezone.utc),
            "12345678-1234-4abc-8def-1234567890ab",
        )

        encoded = cursor.encode()
        decoded = EventCursor.decode(encoded)

        self.assertNotRegex(encoded, r"[^A-Za-z0-9_-]")
        self.assertEqual(decoded, cursor)
        invalid = (
            "",
            "not+a+url/safe/cursor=",
            "a",
            "e30",
            "WyIyMDI2LTA4LTI1VDA3OjA2OjA1WiJd",
            "WyJub3QtYS10aW1lc3RhbXAiLCJub3QtYS11dWlkIl0",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(InvalidFilter) as caught:
                    EventCursor.decode(value)
                self.assertEqual(caught.exception.field, "cursor")

    def test_cursor_rejects_timestamps_that_overflow_during_utc_normalization(self):
        boundary_timestamps = (
            "0001-01-01T00:00:00+14:00",
            "9999-12-31T23:59:59-14:00",
        )

        for timestamp in boundary_timestamps:
            with self.subTest(timestamp=timestamp):
                try:
                    EventCursor.decode(encode_cursor_fixture(timestamp))
                except InvalidFilter as error:
                    self.assertEqual(error.field, "cursor")
                except Exception as error:
                    self.fail(
                        "cursor normalization escaped as "
                        f"{type(error).__name__}"
                    )
                else:
                    self.fail("out-of-range cursor was accepted")

    def test_detail_uses_timestamp_uuid_keyset_and_never_offset(self):
        cursor = EventCursor(
            datetime(2026, 8, 25, 7, 6, 5, 123456, tzinfo=timezone.utc),
            "12345678-1234-4abc-8def-1234567890ab",
        )

        spec = detail_query(AnalyticsFilters(), cursor)

        self.assertFalse(spec.use_cache)
        self.assertIn("timestamp < toDateTime64('2026-08-25T07:06:05.123456Z'", spec.query)
        self.assertIn("timestamp = toDateTime64('2026-08-25T07:06:05.123456Z'", spec.query)
        self.assertIn("uuid < toUUID('12345678-1234-4abc-8def-1234567890ab')", spec.query)
        self.assertIn("ORDER BY timestamp DESC, uuid DESC", spec.query)
        self.assertIn("LIMIT 100", spec.query)
        self.assertNotIn("OFFSET", spec.query.upper())

    def test_export_detail_uses_the_same_keyset_with_a_fixed_thousand_row_page(self):
        cursor = EventCursor(
            datetime(2026, 8, 25, 7, 6, 5, 123456, tzinfo=timezone.utc),
            "12345678-1234-4abc-8def-1234567890ab",
        )

        spec = export_detail_query(AnalyticsFilters(), cursor)

        self.assertEqual(spec.name, "site-admin-export-events")
        self.assertFalse(spec.use_cache)
        self.assertIn("timestamp < toDateTime64('2026-08-25T07:06:05.123456Z'", spec.query)
        self.assertIn("uuid < toUUID('12345678-1234-4abc-8def-1234567890ab')", spec.query)
        self.assertIn("ORDER BY timestamp DESC, uuid DESC", spec.query)
        self.assertIn("LIMIT 1000", spec.query)
        self.assertNotIn("OFFSET", spec.query.upper())


class AnalyticsRouteTests(unittest.TestCase):
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

    def login(self):
        return self.client.post(
            "/login",
            data={"password": "fixture-admin-password"},
        )

    def test_all_api_routes_require_the_administrator_session(self):
        routes = (
            "/api/summary",
            "/api/trend",
            "/api/rankings?kind=page",
            "/api/events",
        )

        with patch("requests.Session.post") as post:
            for route in routes:
                with self.subTest(route=route):
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.headers.get("Cache-Control"), "no-store")
                    self.assertEqual(response.get_json(), {"error": "Authentication required."})
        post.assert_not_called()

    def test_fixed_routes_return_normalized_json_and_disable_storage(self):
        self.login()
        responses = (
            StubResponse(payload={"columns": ["total_events"], "results": [[3]]}),
            StubResponse(payload={"columns": ["day", "pageviews"], "results": [["2026-08-25", 2]]}),
            StubResponse(payload={"columns": ["value", "count"], "results": [["/", 2]]}),
            StubResponse(payload={"columns": ["timestamp", "uuid"], "results": []}),
        )
        routes = (
            "/api/summary?days=7",
            "/api/trend?days=7",
            "/api/rankings?days=7&kind=page",
            "/api/events?days=7",
        )

        with patch("requests.Session.post", side_effect=responses):
            payloads = []
            for route in routes:
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("Cache-Control"), "no-store")
                payloads.append(response.get_json())

        self.assertEqual(payloads[0], {"columns": ["total_events"], "results": [[3]]})
        self.assertEqual(
            payloads[1],
            {"columns": ["day", "pageviews"], "results": [["2026-08-25", 2]]},
        )
        self.assertEqual(
            payloads[2],
            {"columns": ["value", "count"], "results": [["/", 2]]},
        )
        self.assertEqual(
            payloads[3],
            {"columns": ["timestamp", "uuid"], "results": [], "next_cursor": None},
        )

    def test_invalid_filters_return_400_without_contacting_posthog_or_leaking_sql(self):
        self.login()
        attacks = (
            "/api/summary?days=91",
            "/api/trend?event=custom",
            "/api/rankings?kind=page%3BSELECT%201",
            "/api/events?path=/ok%27%20OR%201=1--",
            "/api/events?cursor=not+a+cursor",
        )

        with patch("requests.Session.post") as post:
            for route in attacks:
                with self.subTest(route=route):
                    response = self.client.get(route)
                    body = response.get_json()
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.headers.get("Cache-Control"), "no-store")
                    self.assertEqual(set(body), {"error", "field"})
                    self.assertNotIn("SELECT", response.get_data(as_text=True).upper())
                    self.assertNotIn("fixture-personal-key", response.get_data(as_text=True))
        post.assert_not_called()

    def test_scoped_control_character_ip_returns_safe_400_instead_of_500(self):
        self.login()
        hostile_ip = "fe80::1%scope\nbad"

        with patch("requests.Session.post") as post:
            try:
                response = self.client.get(
                    "/api/summary",
                    query_string={"ip": hostile_ip},
                )
            except Exception as error:
                self.fail(
                    "IP validation escaped the route boundary as "
                    f"{type(error).__name__}"
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["field"], "ip")
        body = response.get_data(as_text=True)
        self.assertNotIn("fixture-personal-key", body)
        self.assertNotIn("SELECT", body.upper())
        self.assertNotIn(hostile_ip, body)
        post.assert_not_called()

    def test_upstream_failures_return_one_safe_503_body(self):
        self.login()
        routes = (
            "/api/summary",
            "/api/trend",
            "/api/rankings?kind=page",
            "/api/events",
        )

        with patch(
            "requests.Session.post",
            side_effect=requests.Timeout("fixture-personal-key SELECT secret"),
        ):
            for route in routes:
                with self.subTest(route=route):
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(
                        response.get_json(),
                        {"error": "PostHog 暂时不可用，请稍后重试。"},
                    )
                    body = response.get_data(as_text=True)
                    self.assertNotIn("fixture-personal-key", body)
                    self.assertNotIn("SELECT", body.upper())

    def test_detail_response_cursor_uses_the_final_timestamp_uuid_pair(self):
        self.login()
        rows = []
        for index in range(100):
            rows.append(
                [
                    f"2026-08-25T07:06:{59 - (index % 60):02d}+00:00",
                    f"00000000-0000-4000-8000-{index:012d}",
                ]
            )
        response = StubResponse(
            payload={"columns": ["timestamp", "uuid"], "results": rows}
        )

        with patch("requests.Session.post", return_value=response):
            result = self.client.get("/api/events")

        self.assertEqual(result.status_code, 200)
        payload = result.get_json()
        cursor = EventCursor.decode(payload["next_cursor"])
        self.assertEqual(cursor.timestamp.isoformat(), "2026-08-25T07:06:20+00:00")
        self.assertEqual(cursor.uuid, "00000000-0000-4000-8000-000000000099")

    def test_detail_rejects_an_upstream_result_over_the_hard_row_limit(self):
        self.login()
        rows = [
            [
                "2026-08-25T07:06:05+00:00",
                f"00000000-0000-4000-8000-{index:012d}",
            ]
            for index in range(101)
        ]
        response = StubResponse(
            payload={"columns": ["timestamp", "uuid"], "results": rows}
        )

        with patch("requests.Session.post", return_value=response):
            result = self.client.get("/api/events")

        self.assertEqual(result.status_code, 503)
        self.assertEqual(
            result.get_json(),
            {"error": "PostHog 暂时不可用，请稍后重试。"},
        )

    def test_cursor_utc_underflow_returns_safe_400_instead_of_500(self):
        self.login()
        cursor = encode_cursor_fixture("0001-01-01T00:00:00+14:00")

        with patch("requests.Session.post") as post:
            try:
                response = self.client.get(
                    "/api/events",
                    query_string={"cursor": cursor},
                )
            except Exception as error:
                self.fail(
                    "request cursor boundary escaped as "
                    f"{type(error).__name__}"
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["field"], "cursor")
        self.assertNotIn("0001-01-01", response.get_data(as_text=True))
        post.assert_not_called()

    def test_final_upstream_cursor_utc_underflow_returns_safe_503(self):
        self.login()
        rows = [
            [
                "2026-08-25T07:06:05+00:00",
                f"00000000-0000-4000-8000-{index:012d}",
            ]
            for index in range(99)
        ]
        rows.append(
            [
                "0001-01-01T00:00:00+14:00",
                "12345678-1234-4abc-8def-1234567890ab",
            ]
        )
        upstream = StubResponse(
            payload={"columns": ["timestamp", "uuid"], "results": rows}
        )

        with patch("requests.Session.post", return_value=upstream):
            try:
                response = self.client.get("/api/events")
            except Exception as error:
                self.fail(
                    "upstream cursor boundary escaped as "
                    f"{type(error).__name__}"
                )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {"error": "PostHog 暂时不可用，请稍后重试。"},
        )
        body = response.get_data(as_text=True)
        self.assertNotIn("0001-01-01", body)
        self.assertNotIn("fixture-personal-key", body)

    def test_no_arbitrary_sql_api_is_registered(self):
        paths = {rule.rule for rule in self.app.url_map.iter_rules()}

        self.assertEqual(
            {path for path in paths if path.startswith("/api/")},
            {"/api/summary", "/api/trend", "/api/rankings", "/api/events"},
        )


if __name__ == "__main__":
    unittest.main()
