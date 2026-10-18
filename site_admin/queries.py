import base64
import binascii
import ipaddress
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from uuid import UUID


_APPROVED_EVENT_NAMES = (
    "$pageview",
    "primary_click",
    "language_changed",
    "theme_changed",
    "comic_viewer_entered",
    "comic_opened",
    "training_started",
)
APPROVED_EVENTS = frozenset(_APPROVED_EVENT_NAMES)
ALLOWED_DAYS = frozenset({1, 7, 30, 90})
_COMIC_ID = re.compile(r"[0-9]{1,16}\Z")
_IP_TEXT = re.compile(r"[0-9A-Fa-f:.]{2,45}\Z")
_URLSAFE_CURSOR = re.compile(r"[A-Za-z0-9_-]{2,512}\Z")
_CANONICAL_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
_CURSOR_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)


class InvalidFilter(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def _contains_control(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
        for character in value
    )


def quote_hogql_string(value: str) -> str:
    if not isinstance(value, str) or _contains_control(value):
        raise ValueError("HogQL string contains an invalid control character")
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


_APPROVED_EVENT_PREDICATE = (
    "event IN ("
    + ", ".join(quote_hogql_string(event) for event in _APPROVED_EVENT_NAMES)
    + ")"
)


@dataclass(frozen=True)
class AnalyticsFilters:
    days: int = 30
    ip: str | None = None
    path: str | None = None
    event: str | None = None
    comic_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.days) is not int or self.days not in ALLOWED_DAYS:
            raise InvalidFilter("days", "days must be one of 1, 7, 30, or 90")

        if self.ip is not None:
            if (
                not isinstance(self.ip, str)
                or _IP_TEXT.fullmatch(self.ip) is None
            ):
                raise InvalidFilter("ip", "ip must be a valid IP address")
            try:
                canonical_ip = str(ipaddress.ip_address(self.ip))
            except ValueError:
                raise InvalidFilter("ip", "ip must be a valid IP address") from None
            object.__setattr__(self, "ip", canonical_ip)

        if self.path is not None:
            if not isinstance(self.path, str):
                raise InvalidFilter("path", "path is invalid")
            forbidden = (
                "'",
                '"',
                "\\",
                ";",
                "--",
                "/*",
                "*/",
                "#",
            )
            if (
                not self.path.startswith("/")
                or len(self.path) > 200
                or _contains_control(self.path)
                or any(marker in self.path for marker in forbidden)
            ):
                raise InvalidFilter("path", "path is invalid")

        if self.event is not None and self.event not in APPROVED_EVENTS:
            raise InvalidFilter("event", "event is not approved")

        if self.comic_id is not None and (
            not isinstance(self.comic_id, str)
            or _COMIC_ID.fullmatch(self.comic_id) is None
        ):
            raise InvalidFilter("comic_id", "comic_id must contain 1 to 16 digits")

    @classmethod
    def from_request(cls, args: Mapping[str, str]) -> "AnalyticsFilters":
        days_value = args.get("days", "30")
        if not isinstance(days_value, str) or days_value not in {
            "1",
            "7",
            "30",
            "90",
        }:
            raise InvalidFilter("days", "days must be one of 1, 7, 30, or 90")

        return cls(
            days=int(days_value),
            ip=_optional_request_value(args, "ip"),
            path=_optional_request_value(args, "path"),
            event=_optional_request_value(args, "event"),
            comic_id=_optional_request_value(args, "comic_id"),
        )


def _optional_request_value(args: Mapping[str, str], name: str) -> str | None:
    value = args.get(name)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvalidFilter(name, f"{name} is invalid")
    return value


@dataclass(frozen=True)
class EventCursor:
    timestamp: datetime
    uuid: str

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime) or self.timestamp.tzinfo is None:
            raise ValueError("cursor timestamp must include a timezone")
        if not isinstance(self.uuid, str) or _CANONICAL_UUID.fullmatch(self.uuid) is None:
            raise ValueError("cursor uuid must be canonical")
        try:
            canonical_uuid = str(UUID(self.uuid))
        except ValueError:
            raise ValueError("cursor uuid must be canonical") from None
        if canonical_uuid != self.uuid:
            raise ValueError("cursor uuid must be canonical")
        try:
            normalized_timestamp = self.timestamp.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise ValueError("cursor timestamp is outside the supported range") from None
        object.__setattr__(self, "timestamp", normalized_timestamp)

    def encode(self) -> str:
        timestamp = self.timestamp.isoformat(timespec="microseconds").replace(
            "+00:00",
            "Z",
        )
        raw = json.dumps(
            [timestamp, self.uuid],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, value: str) -> "EventCursor":
        try:
            if not isinstance(value, str) or _URLSAFE_CURSOR.fullmatch(value) is None:
                raise ValueError
            if len(value) % 4 == 1:
                raise ValueError
            padded = value + "=" * (-len(value) % 4)
            raw = base64.b64decode(
                padded,
                altchars=b"-_",
                validate=True,
            )
            if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
                raise ValueError
            payload = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(payload, list)
                or len(payload) != 2
                or not all(isinstance(item, str) for item in payload)
            ):
                raise ValueError
            timestamp_value, uuid_value = payload
            if _CURSOR_TIMESTAMP.fullmatch(timestamp_value) is None:
                raise ValueError
            timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
            return cls(timestamp=timestamp, uuid=uuid_value)
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
            raise InvalidFilter("cursor", "cursor is invalid") from None


@dataclass(frozen=True)
class QuerySpec:
    name: str
    query: str
    use_cache: bool


_SEPARATOR = quote_hogql_string(" / ")
RANKING_EXPRESSIONS = MappingProxyType(
    {
        "page": "properties.path",
        "referrer": "properties.referrer",
        "country": (
            "concat(toString(properties.$geoip_country_name), "
            f"{_SEPARATOR}, toString(properties.$geoip_city_name))"
        ),
        "device": (
            "concat(toString(properties.$device_type), "
            f"{_SEPARATOR}, toString(properties.$browser), {_SEPARATOR}, "
            "toString(properties.$os))"
        ),
        "operation": (
            "concat(toString(properties.control_id), "
            f"{_SEPARATOR}, toString(properties.label))"
        ),
        "comic": "properties.comic_id",
    }
)
_RANKING_EVENTS = MappingProxyType(
    {
        "page": "$pageview",
        "referrer": "$pageview",
        "country": "$pageview",
        "device": "$pageview",
        "operation": "primary_click",
        "comic": "comic_opened",
    }
)


def _where_conditions(filters: AnalyticsFilters) -> list[str]:
    if not isinstance(filters, AnalyticsFilters):
        raise TypeError("filters must be AnalyticsFilters")
    conditions = [
        f"timestamp >= now() - INTERVAL {filters.days} DAY",
        "timestamp >= now() - INTERVAL 90 DAY",
        _APPROVED_EVENT_PREDICATE,
    ]
    if filters.ip is not None:
        conditions.append(
            f"toString(properties.$ip) = {quote_hogql_string(filters.ip)}"
        )
    if filters.path is not None:
        conditions.append(
            f"toString(properties.path) = {quote_hogql_string(filters.path)}"
        )
    if filters.event is not None:
        conditions.append(f"event = {quote_hogql_string(filters.event)}")
    if filters.comic_id is not None:
        conditions.append(
            "toString(properties.comic_id) = "
            f"{quote_hogql_string(filters.comic_id)}"
        )
    return conditions


def _where_sql(conditions: list[str]) -> str:
    return "\n  AND ".join(conditions)


def summary_query(filters: AnalyticsFilters) -> QuerySpec:
    pageview = quote_hogql_string("$pageview")
    primary_click = quote_hogql_string("primary_click")
    comic_entry = quote_hogql_string("comic_viewer_entered")
    comic_opened = quote_hogql_string("comic_opened")
    query = f"""SELECT
  count() AS total_events,
  countIf(event = {pageview}) AS pageviews,
  uniqExact(distinct_id) AS unique_visitors,
  uniqExactIf(toString(properties.$ip), notEmpty(toString(properties.$ip))) AS unique_ips,
  countIf(event = {primary_click}) AS primary_clicks,
  countIf(event = {comic_entry}) AS comic_entries,
  uniqExactIf(
    toString(properties.comic_id),
    event = {comic_opened} AND notEmpty(toString(properties.comic_id))
  ) AS unique_comic_ids
FROM events
WHERE {_where_sql(_where_conditions(filters))}"""
    return QuerySpec("site-admin-summary", query, True)


def trend_query(filters: AnalyticsFilters) -> QuerySpec:
    pageview = quote_hogql_string("$pageview")
    conditions = _where_conditions(filters)
    conditions.append(f"event = {pageview}")
    query = f"""SELECT
  toDate(timestamp) AS day,
  count() AS pageviews
FROM events
WHERE {_where_sql(conditions)}
GROUP BY day
ORDER BY day ASC"""
    return QuerySpec("site-admin-trend", query, True)


def ranking_query(kind: str, filters: AnalyticsFilters) -> QuerySpec:
    expression = RANKING_EXPRESSIONS.get(kind)
    if expression is None:
        raise InvalidFilter("kind", "kind is not approved")
    conditions = _where_conditions(filters)
    conditions.append(f"event = {quote_hogql_string(_RANKING_EVENTS[kind])}")
    conditions.append(f"notEmpty(toString({expression}))")
    query = f"""SELECT
  {expression} AS value,
  count() AS count
FROM events
WHERE {_where_sql(conditions)}
GROUP BY value
ORDER BY count DESC, value ASC
LIMIT 20"""
    return QuerySpec(f"site-admin-ranking-{kind}", query, True)


def _detail_query(
    filters: AnalyticsFilters,
    cursor: EventCursor | None,
    *,
    limit: int,
    name: str,
) -> QuerySpec:
    conditions = _where_conditions(filters)
    if cursor is not None:
        if not isinstance(cursor, EventCursor):
            raise InvalidFilter("cursor", "cursor is invalid")
        timestamp = cursor.timestamp.isoformat(timespec="microseconds").replace(
            "+00:00",
            "Z",
        )
        timestamp_literal = quote_hogql_string(timestamp)
        timezone_literal = quote_hogql_string("UTC")
        uuid_literal = quote_hogql_string(cursor.uuid)
        timestamp_expression = (
            f"toDateTime64({timestamp_literal}, 6, {timezone_literal})"
        )
        conditions.append(
            "(timestamp < "
            f"{timestamp_expression} OR (timestamp = {timestamp_expression} "
            f"AND uuid < toUUID({uuid_literal})))"
        )
    query = f"""SELECT
  timestamp,
  uuid,
  properties.$ip AS ip,
  event,
  properties.path AS path,
  properties.referrer AS referrer,
  properties.$device_type AS device,
  properties.$browser AS browser,
  properties.$os AS os,
  properties.$geoip_country_name AS country,
  properties.$geoip_city_name AS city,
  properties.comic_id AS comic_id
FROM events
WHERE {_where_sql(conditions)}
ORDER BY timestamp DESC, uuid DESC
LIMIT {limit}"""
    return QuerySpec(name, query, False)


def detail_query(
    filters: AnalyticsFilters,
    cursor: EventCursor | None,
) -> QuerySpec:
    return _detail_query(
        filters,
        cursor,
        limit=100,
        name="site-admin-events",
    )


def export_detail_query(
    filters: AnalyticsFilters,
    cursor: EventCursor | None,
) -> QuerySpec:
    return _detail_query(
        filters,
        cursor,
        limit=1000,
        name="site-admin-export-events",
    )
