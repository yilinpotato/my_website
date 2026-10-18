import io
import logging
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import requests

from site_admin.config import Settings
from site_admin.posthog_client import (
    PostHogClient,
    PostHogUnavailable,
    QueryResult,
)


class StubResponse:
    def __init__(self, *, status=200, payload=None, json_error=None, headers=None):
        self.status_code = status
        self._payload = payload
        self._json_error = json_error
        self.headers = headers or {}

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class PostHogClientTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            admin_password="fixture-admin-password",
            session_secret="s" * 40,
            posthog_personal_key="fixture-personal-key",
            posthog_project_id=576032,
        )

    def test_posts_exact_query_contract_and_normalizes_the_result(self):
        response = StubResponse(
            payload={
                "columns": ["answer", "label"],
                "results": [[1, "one"], [2, None]],
            }
        )
        client = PostHogClient(self.settings)

        with patch("requests.Session.post", return_value=response) as post:
            result = client.run_hogql(
                "SELECT 1",
                "site-admin-healthcheck",
                use_cache=False,
            )

        self.assertEqual(
            result,
            QueryResult(
                columns=("answer", "label"),
                results=((1, "one"), (2, None)),
            ),
        )
        post.assert_called_once_with(
            "https://us.posthog.com/api/projects/576032/query/",
            headers={"Authorization": "Bearer fixture-personal-key"},
            json={
                "query": {
                    "kind": "HogQLQuery",
                    "query": "SELECT 1",
                    "name": "site-admin-healthcheck",
                },
                "name": "site-admin-healthcheck",
            },
            timeout=10,
            allow_redirects=False,
        )

    def test_accepts_only_a_finite_positive_timeout_at_most_ten_seconds(self):
        response = StubResponse(payload={"columns": [], "results": []})
        client = PostHogClient(self.settings)

        with patch("requests.Session.post", return_value=response) as post:
            client.run_hogql(
                "SELECT 1",
                "site-admin-export-events",
                use_cache=False,
                timeout=2.5,
            )

        self.assertEqual(post.call_args.kwargs["timeout"], 2.5)

        for timeout in (True, False, 0, -1, 10.0001, float("nan"), float("inf"), "1"):
            with self.subTest(timeout=timeout):
                with patch("requests.Session.post") as post:
                    with self.assertRaises(ValueError):
                        client.run_hogql(
                            "SELECT 1",
                            "site-admin-export-events",
                            use_cache=False,
                            timeout=timeout,
                        )
                post.assert_not_called()

    def test_redirect_response_is_safe_and_does_not_leak_its_location(self):
        response = StubResponse(
            status=307,
            payload={"redirect": "body must not be returned"},
            headers={
                "X-Request-ID": "redirect-307",
                "Location": (
                    "https://outside.example/private?key=fixture-personal-key"
                    "&path=/private-filter"
                ),
            },
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("site_admin.posthog")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        client = PostHogClient(self.settings)

        try:
            with patch("requests.Session.post", return_value=response):
                with self.assertRaises(PostHogUnavailable) as caught:
                    client.run_hogql(
                        "SELECT '203.0.113.8/private-filter'",
                        "details",
                        use_cache=False,
                    )
        finally:
            logger.removeHandler(handler)

        output = stream.getvalue()
        self.assertEqual(
            str(caught.exception),
            "PostHog 暂时不可用，请稍后重试。",
        )
        self.assertIn("status=307 request_id=redirect-307", output)
        for forbidden in (
            "outside.example",
            "fixture-personal-key",
            "203.0.113.8",
            "/private-filter",
            "body must not be returned",
        ):
            self.assertNotIn(forbidden, output)
            self.assertNotIn(forbidden, str(caught.exception))

    def test_rejects_malformed_upstream_columns_and_rows(self):
        malformed_payloads = (
            None,
            [],
            {"columns": "answer", "results": [[1]]},
            {"columns": ["answer", 2], "results": [[1, 2]]},
            {"columns": ["answer"], "results": "not-rows"},
            {"columns": ["answer"], "results": [1]},
            {"columns": ["answer"], "results": [[1, 2]]},
            {"columns": ["answer"], "results": [[["nested"]]]},
            {"columns": ["answer"], "results": [[{"nested": True}]]},
            {"columns": ["answer"], "results": [[float("nan")]]},
        )

        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                client = PostHogClient(self.settings)
                with patch(
                    "requests.Session.post",
                    return_value=StubResponse(payload=payload),
                ):
                    with self.assertRaises(PostHogUnavailable) as caught:
                        client.run_hogql("SELECT 1", "malformed", use_cache=False)

                self.assertEqual(
                    str(caught.exception),
                    "PostHog 暂时不可用，请稍后重试。",
                )

    def test_timeout_raises_only_a_safe_local_error_and_redacts_inputs(self):
        secret_query = "SELECT '203.0.113.8/private-path'"
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("site_admin.posthog")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        client = PostHogClient(self.settings)

        try:
            with patch(
                "requests.Session.post",
                side_effect=requests.Timeout(
                    "fixture-personal-key SELECT 203.0.113.8/private-path"
                ),
            ):
                with self.assertRaises(PostHogUnavailable) as caught:
                    client.run_hogql(secret_query, "details", use_cache=False)
        finally:
            logger.removeHandler(handler)

        output = stream.getvalue()
        self.assertEqual(
            str(caught.exception),
            "PostHog 暂时不可用，请稍后重试。",
        )
        self.assertIn("status=timeout", output)
        self.assertIn("request_id=unknown", output)
        for forbidden in (
            "fixture-personal-key",
            secret_query,
            "203.0.113.8",
            "/private-path",
        ):
            self.assertNotIn(forbidden, output)
            self.assertNotIn(forbidden, str(caught.exception))

    def test_http_and_non_json_failures_log_only_safe_upstream_metadata(self):
        cases = (
            StubResponse(
                status=502,
                payload={"query": "SELECT secret", "key": "fixture-personal-key"},
                headers={"X-Request-ID": "request-502"},
            ),
            StubResponse(
                status=200,
                json_error=ValueError("fixture-personal-key SELECT secret"),
                headers={"X-Request-ID": "request-json"},
            ),
            StubResponse(
                status=503,
                payload={},
                headers={"X-Request-ID": "bad\nrequest fixture-personal-key"},
            ),
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("site_admin.posthog")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        try:
            for response in cases:
                with self.subTest(status=response.status_code):
                    client = PostHogClient(self.settings)
                    with patch("requests.Session.post", return_value=response):
                        with self.assertRaises(PostHogUnavailable):
                            client.run_hogql(
                                "SELECT '203.0.113.8/private-path'",
                                "unsafe-name",
                                use_cache=False,
                            )
        finally:
            logger.removeHandler(handler)

        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("status=502 request_id=request-502", lines[0])
        self.assertIn("status=200 request_id=request-json", lines[1])
        self.assertIn("status=503 request_id=unknown", lines[2])
        for forbidden in (
            "fixture-personal-key",
            "SELECT",
            "203.0.113.8",
            "/private-path",
            "bad",
        ):
            self.assertNotIn(forbidden, stream.getvalue())

    def test_request_id_equal_to_or_containing_the_personal_key_is_redacted(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("site_admin.posthog")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        try:
            for request_id in (
                "fixture-personal-key",
                "trace-fixture-personal-key-tail",
            ):
                with self.subTest(request_id=request_id):
                    client = PostHogClient(self.settings)
                    with patch(
                        "requests.Session.post",
                        return_value=StubResponse(
                            status=502,
                            payload={},
                            headers={"X-Request-ID": request_id},
                        ),
                    ):
                        with self.assertRaises(PostHogUnavailable):
                            client.run_hogql("SELECT 1", "details", use_cache=False)
        finally:
            logger.removeHandler(handler)

        output = stream.getvalue()
        self.assertEqual(output.count("request_id=unknown"), 2)
        self.assertNotIn("fixture-personal-key", output)

    def test_successful_aggregate_queries_are_cached_for_sixty_seconds(self):
        now = [100.0]
        responses = [
            StubResponse(payload={"columns": ["value"], "results": [[1]]}),
            StubResponse(payload={"columns": ["value"], "results": [[2]]}),
        ]
        client = PostHogClient(self.settings, clock=lambda: now[0])

        with patch("requests.Session.post", side_effect=responses) as post:
            first = client.run_hogql("SELECT 1", "summary")
            now[0] = 159.999
            cached = client.run_hogql("SELECT 1", "summary")
            now[0] = 160.0
            refreshed = client.run_hogql("SELECT 1", "summary")

        self.assertEqual(first.results, ((1,),))
        self.assertIs(cached, first)
        self.assertEqual(refreshed.results, ((2,),))
        self.assertEqual(post.call_count, 2)

    def test_detail_queries_and_errors_are_never_cached(self):
        success = StubResponse(payload={"columns": ["value"], "results": [[1]]})
        client = PostHogClient(self.settings)

        with patch(
            "requests.Session.post",
            side_effect=[requests.Timeout("first"), success, success, success],
        ) as post:
            with self.assertRaises(PostHogUnavailable):
                client.run_hogql("SELECT 1", "summary")
            aggregate = client.run_hogql("SELECT 1", "summary")
            detail_first = client.run_hogql("SELECT 2", "events", use_cache=False)
            detail_second = client.run_hogql("SELECT 2", "events", use_cache=False)

        self.assertEqual(aggregate.results, ((1,),))
        self.assertEqual(detail_first.results, ((1,),))
        self.assertEqual(detail_second.results, ((1,),))
        self.assertEqual(post.call_count, 4)

    def test_cache_is_bounded_and_safe_under_parallel_access(self):
        client = PostHogClient(self.settings, max_cache_entries=8)

        def response_for_call(*args, **kwargs):
            query = kwargs["json"]["query"]["query"]
            return StubResponse(payload={"columns": ["query"], "results": [[query]]})

        queries = [f"SELECT {index}" for index in range(32)]
        with patch("requests.Session.post", side_effect=response_for_call) as post:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(
                    executor.map(
                        lambda query: client.run_hogql(query, "ranking-page"),
                        queries,
                    )
                )
            for query, result in zip(queries, results):
                self.assertEqual(result.results, ((query,),))
            calls_after_fill = post.call_count
            for query in queries:
                client.run_hogql(query, "ranking-page")

        self.assertEqual(calls_after_fill, 32)
        self.assertGreaterEqual(post.call_count, 56)
        self.assertLessEqual(post.call_count, 64)


if __name__ == "__main__":
    unittest.main()
