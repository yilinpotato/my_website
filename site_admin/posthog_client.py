import logging
import math
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

import requests

from .config import Settings


POSTHOG_UNAVAILABLE_MESSAGE = "PostHog 暂时不可用，请稍后重试。"
CACHE_SECONDS = 60.0
DEFAULT_MAX_CACHE_ENTRIES = 128
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_LOGGER = logging.getLogger("site_admin.posthog")
_LOGGER.addHandler(logging.NullHandler())


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    results: tuple[tuple[object, ...], ...]


class PostHogUnavailable(RuntimeError):
    pass


class PostHogClient:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_cache_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
    ) -> None:
        if max_cache_entries < 1:
            raise ValueError("max_cache_entries must be positive")
        self._url = (
            f"{settings.posthog_api_host}/api/projects/"
            f"{settings.posthog_project_id}/query/"
        )
        self._authorization = f"Bearer {settings.posthog_personal_key}"
        self._personal_key = settings.posthog_personal_key
        self._session = requests.Session()
        self._clock = clock
        self._max_cache_entries = max_cache_entries
        self._cache: OrderedDict[
            tuple[str, str], tuple[float, QueryResult]
        ] = OrderedDict()
        self._cache_lock = RLock()

    def run_hogql(
        self,
        query: str,
        name: str,
        *,
        use_cache: bool = True,
        timeout: int | float = 10,
    ) -> QueryResult:
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > 10
        ):
            raise ValueError("timeout must be a finite number from 0 through 10")
        cache_key = (name, query)
        if use_cache:
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

        try:
            response = self._session.post(
                self._url,
                headers={"Authorization": self._authorization},
                json={
                    "query": {
                        "kind": "HogQLQuery",
                        "query": query,
                        "name": name,
                    },
                    "name": name,
                },
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            self._raise_unavailable("timeout", "unknown")
        except requests.RequestException as error:
            status, request_id = self._metadata_from_exception(error)
            self._raise_unavailable(status, request_id)

        status = self._safe_status(response.status_code)
        request_id = self._request_id(response.headers)
        if not isinstance(response.status_code, int) or not 200 <= response.status_code < 300:
            self._raise_unavailable(status, request_id)

        try:
            payload = response.json()
            result = self._normalize(payload)
        except (TypeError, ValueError):
            self._raise_unavailable(status, request_id)

        if use_cache:
            self._store_cached(cache_key, result)
        return result

    def _get_cached(self, key: tuple[str, str]) -> QueryResult | None:
        now = self._clock()
        with self._cache_lock:
            self._prune_expired(now)
            cached = self._cache.get(key)
            if cached is None:
                return None
            _, result = cached
            self._cache.move_to_end(key)
            return result

    def _store_cached(self, key: tuple[str, str], result: QueryResult) -> None:
        now = self._clock()
        with self._cache_lock:
            self._prune_expired(now)
            self._cache[key] = (now + CACHE_SECONDS, result)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_entries:
                self._cache.popitem(last=False)

    def _prune_expired(self, now: float) -> None:
        expired = [key for key, (deadline, _) in self._cache.items() if deadline <= now]
        for key in expired:
            self._cache.pop(key, None)

    @staticmethod
    def _normalize(payload: object) -> QueryResult:
        if not isinstance(payload, dict):
            raise ValueError("invalid upstream result")
        columns = payload.get("columns")
        results = payload.get("results")
        if not isinstance(columns, list) or not all(
            isinstance(column, str) for column in columns
        ):
            raise ValueError("invalid upstream columns")
        if not isinstance(results, list):
            raise ValueError("invalid upstream rows")

        normalized_rows: list[tuple[object, ...]] = []
        for row in results:
            if not isinstance(row, list) or len(row) != len(columns):
                raise ValueError("invalid upstream row")
            if not all(PostHogClient._is_result_scalar(value) for value in row):
                raise ValueError("invalid upstream value")
            normalized_rows.append(tuple(row))
        return QueryResult(tuple(columns), tuple(normalized_rows))

    @staticmethod
    def _is_result_scalar(value: object) -> bool:
        if isinstance(value, float):
            return math.isfinite(value)
        return value is None or isinstance(value, (str, int, bool))

    def _metadata_from_exception(
        self,
        error: requests.RequestException,
    ) -> tuple[str, str]:
        response = error.response
        if response is None:
            return "unavailable", "unknown"
        return self._safe_status(response.status_code), self._request_id(response.headers)

    @staticmethod
    def _safe_status(status: object) -> str:
        if isinstance(status, int) and 100 <= status <= 599:
            return str(status)
        return "unknown"

    def _request_id(self, headers: object) -> str:
        if not hasattr(headers, "get"):
            return "unknown"
        value = headers.get("X-Request-ID")
        if (
            isinstance(value, str)
            and self._personal_key not in value
            and _SAFE_REQUEST_ID.fullmatch(value)
        ):
            return value
        return "unknown"

    @staticmethod
    def _raise_unavailable(status: str, request_id: str) -> None:
        _LOGGER.warning(
            "event=posthog_unavailable status=%s request_id=%s",
            status,
            request_id,
        )
        raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
