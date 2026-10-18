import csv
import math
import tempfile
import time
import unicodedata
from datetime import datetime

from .posthog_client import POSTHOG_UNAVAILABLE_MESSAGE, PostHogUnavailable, QueryResult
from .queries import AnalyticsFilters, EventCursor, export_detail_query


CSV_MAX_ROWS = 10_000
CSV_MAX_CELL_BYTES = 4 * 1024
CSV_MAX_BYTES = 16 * 1024 * 1024
CSV_SPOOL_MEMORY_BYTES = 1024 * 1024
CSV_PAGE_SIZE = 1000
CSV_MAX_REQUESTS = 10
CSV_DEADLINE_SECONDS = 25.0
CSV_REQUEST_TIMEOUT_SECONDS = 10.0
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
_FORMULA_PREFIXES = ("=", "+", "-", "@")


class _CsvLimitExceeded(ValueError):
    pass


class _BoundedUtf8Writer:
    def __init__(self, stream, maximum_bytes: int) -> None:
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self.bytes_written = 0

    def write(self, text: str) -> int:
        encoded = text.encode("utf-8")
        if self.bytes_written + len(encoded) > self._maximum_bytes:
            raise _CsvLimitExceeded
        self._stream.write(encoded)
        self.bytes_written += len(encoded)
        return len(text)


def sanitize_csv_cell(value: object) -> str:
    if value is None:
        text = ""
    else:
        text = str(value)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    significant = next(
        (
            character
            for character in text
            if not character.isspace()
            and unicodedata.category(character) not in {"Cc", "Cf"}
        ),
        None,
    )
    if significant in _FORMULA_PREFIXES:
        return "'" + text
    return text


def build_csv_export(
    posthog,
    filters: AnalyticsFilters,
    *,
    clock=time.monotonic,
):
    stream = tempfile.SpooledTemporaryFile(
        max_size=CSV_SPOOL_MEMORY_BYTES,
        mode="w+b",
    )
    sink = _BoundedUtf8Writer(stream, CSV_MAX_BYTES)
    writer = csv.writer(sink, lineterminator="\r\n")
    rows_written = 0
    requests_made = 0
    cursor = None

    try:
        deadline = _clock_reading(clock) + CSV_DEADLINE_SECONDS
        sink.write("\ufeff")
        _write_csv_row(writer, DETAIL_COLUMNS)
        while rows_written < CSV_MAX_ROWS and requests_made < CSV_MAX_REQUESTS:
            remaining_seconds = deadline - _clock_reading(clock)
            if remaining_seconds <= 0:
                raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
            spec = export_detail_query(filters, cursor)
            result = posthog.run_hogql(
                spec.query,
                spec.name,
                use_cache=spec.use_cache,
                timeout=min(CSV_REQUEST_TIMEOUT_SECONDS, remaining_seconds),
            )
            requests_made += 1
            if _clock_reading(clock) >= deadline:
                raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
            _validate_page(result)
            remaining = CSV_MAX_ROWS - rows_written
            page_rows = result.results[:remaining]
            for row in page_rows:
                _write_csv_row(writer, row)
            rows_written += len(page_rows)
            if (
                len(result.results) < CSV_PAGE_SIZE
                or rows_written == CSV_MAX_ROWS
            ):
                break
            next_cursor = _cursor_from_last_row(result)
            if next_cursor == cursor:
                raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
            cursor = next_cursor
        stream.seek(0)
        return stream
    except (ValueError, UnicodeError, csv.Error):
        stream.close()
        raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
    except BaseException:
        stream.close()
        raise


def iter_csv_export(stream):
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        stream.close()


def _clock_reading(clock) -> float:
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value):
        raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
    return float(value)


def _write_csv_row(writer, row: tuple[object, ...]) -> None:
    writer.writerow(_bounded_csv_cell(value) for value in row)


def _bounded_csv_cell(value: object) -> str:
    if isinstance(value, str) and len(value) > CSV_MAX_CELL_BYTES:
        raise _CsvLimitExceeded
    normalized = sanitize_csv_cell(value)
    if (
        len(normalized) > CSV_MAX_CELL_BYTES
        or len(normalized.encode("utf-8")) > CSV_MAX_CELL_BYTES
    ):
        raise _CsvLimitExceeded
    return normalized


def _validate_page(result: object) -> None:
    if not isinstance(result, QueryResult):
        raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
    if result.columns != DETAIL_COLUMNS or len(result.results) > CSV_PAGE_SIZE:
        raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
    for row in result.results:
        if not isinstance(row, tuple) or len(row) != len(DETAIL_COLUMNS):
            raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
        for value in row:
            if isinstance(value, float) and not math.isfinite(value):
                raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None


def _cursor_from_last_row(result: QueryResult) -> EventCursor:
    try:
        timestamp_value = result.results[-1][0]
        uuid_value = result.results[-1][1]
        if not isinstance(timestamp_value, str) or not isinstance(uuid_value, str):
            raise ValueError
        timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
        return EventCursor(timestamp=timestamp, uuid=uuid_value)
    except (ValueError, OverflowError, IndexError):
        raise PostHogUnavailable(POSTHOG_UNAVAILABLE_MESSAGE) from None
