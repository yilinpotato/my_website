"""Check or apply the approved PostHog project settings."""

import argparse
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import requests

if __package__:
    from .config import Settings
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from site_admin.config import Settings


DESIRED_PROJECT_SETTINGS = {
    "timezone": "Asia/Shanghai",
    "anonymize_ips": False,
    "session_recording_opt_in": False,
    "capture_console_log_opt_in": False,
    "capture_performance_opt_in": False,
    "event_retention_months": 12,
    "app_urls": ["https://potatoma.me"],
}

_LOGGER = logging.getLogger("site_admin.configure_posthog")
_LOGGER.addHandler(logging.NullHandler())
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SAFE_ORGANIZATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}\Z")
_MISSING = object()


class PostHogConfigurationError(RuntimeError):
    """A safe, operator-facing project configuration failure."""


def configure_project(settings: Settings, *, apply: bool = False) -> dict:
    """Return required changes, applying and verifying them only when requested."""
    project_id = settings.posthog_project_id
    organization_id = settings.posthog_organization_id
    if (
        isinstance(project_id, bool)
        or not isinstance(project_id, int)
        or project_id < 1
    ):
        raise PostHogConfigurationError("PostHog project configuration has invalid ID")
    if not isinstance(organization_id, str) or not _SAFE_ORGANIZATION_ID.fullmatch(
        organization_id
    ):
        raise PostHogConfigurationError(
            f"PostHog project {project_id} has invalid organization configuration"
        )
    url = (
        f"{settings.posthog_api_host}/api/organizations/"
        f"{organization_id}/projects/{project_id}/"
    )
    headers = {"Authorization": f"Bearer {settings.posthog_personal_key}"}
    session = requests.Session()
    current = _get_project(
        session,
        url,
        headers,
        project_id,
        settings.posthog_personal_key,
    )
    changes = _differences(current)

    if not apply or not changes:
        return changes

    _patch_project(
        session,
        url,
        headers,
        project_id,
        settings.posthog_personal_key,
    )
    verified = _get_project(
        session,
        url,
        headers,
        project_id,
        settings.posthog_personal_key,
    )
    mismatched = tuple(
        name
        for name, desired in DESIRED_PROJECT_SETTINGS.items()
        if not _exact_json_equal(verified.get(name, _MISSING), desired)
    )
    if mismatched:
        fields = ", ".join(mismatched)
        _LOGGER.warning(
            "event=posthog_project_verification_failed project_id=%s fields=%s",
            project_id,
            fields,
        )
        raise PostHogConfigurationError(
            f"PostHog project {project_id} verification failed for fields: {fields}"
        )
    return changes


def _get_project(
    session: requests.Session,
    url: str,
    headers: Mapping[str, str],
    project_id: int,
    sensitive: str,
) -> dict:
    try:
        response = session.get(
            url,
            headers=dict(headers),
            timeout=10,
            allow_redirects=False,
        )
    except requests.Timeout:
        _raise_request_error("GET", project_id, "timeout", "unknown")
    except requests.RequestException as error:
        _raise_request_error(
            "GET",
            project_id,
            _exception_status(error),
            _exception_request_id(error, sensitive),
        )

    _require_success("GET", project_id, response, sensitive)
    try:
        payload = response.json()
    except (TypeError, ValueError):
        _raise_request_error(
            "GET",
            project_id,
            _safe_status(response.status_code),
            _request_id(response.headers, sensitive),
        )
    if not isinstance(payload, dict):
        _raise_request_error(
            "GET",
            project_id,
            _safe_status(response.status_code),
            _request_id(response.headers, sensitive),
        )
    return payload


def _patch_project(
    session: requests.Session,
    url: str,
    headers: Mapping[str, str],
    project_id: int,
    sensitive: str,
) -> None:
    try:
        response = session.patch(
            url,
            headers=dict(headers),
            json=dict(DESIRED_PROJECT_SETTINGS),
            timeout=10,
            allow_redirects=False,
        )
    except requests.Timeout:
        _raise_request_error("PATCH", project_id, "timeout", "unknown")
    except requests.RequestException as error:
        _raise_request_error(
            "PATCH",
            project_id,
            _exception_status(error),
            _exception_request_id(error, sensitive),
        )
    _require_success("PATCH", project_id, response, sensitive)


def _differences(current: Mapping[str, object]) -> dict:
    return {
        name: (
            "<missing>" if current.get(name, _MISSING) is _MISSING else current[name],
            desired,
        )
        for name, desired in DESIRED_PROJECT_SETTINGS.items()
        if not _exact_json_equal(current.get(name, _MISSING), desired)
    }


def _exact_json_equal(current: object, desired: object) -> bool:
    if type(current) is not type(desired):
        return False
    if isinstance(desired, list):
        return len(current) == len(desired) and all(
            _exact_json_equal(current_item, desired_item)
            for current_item, desired_item in zip(current, desired)
        )
    return current == desired


def _require_success(
    action: str,
    project_id: int,
    response: object,
    sensitive: str,
) -> None:
    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int) or not 200 <= status_code < 300:
        _raise_request_error(
            action,
            project_id,
            _safe_status(status_code),
            _request_id(getattr(response, "headers", None), sensitive),
        )


def _exception_status(error: requests.RequestException) -> str:
    response = error.response
    if response is None:
        return "unavailable"
    return _safe_status(response.status_code)


def _exception_request_id(error: requests.RequestException, sensitive: str) -> str:
    response = error.response
    if response is None:
        return "unknown"
    return _request_id(response.headers, sensitive)


def _safe_status(status: object) -> str:
    if isinstance(status, int) and 100 <= status <= 599:
        return str(status)
    return "unknown"


def _request_id(headers: object, sensitive: str) -> str:
    if not hasattr(headers, "get"):
        return "unknown"
    request_id = headers.get("X-Request-ID")
    if (
        isinstance(request_id, str)
        and _SAFE_REQUEST_ID.fullmatch(request_id)
        and sensitive not in request_id
    ):
        return request_id
    return "unknown"


def _raise_request_error(
    action: str,
    project_id: int,
    status: str,
    request_id: str,
) -> None:
    _LOGGER.warning(
        "event=posthog_project_request_failed action=%s project_id=%s "
        "status=%s request_id=%s",
        action,
        project_id,
        status,
        request_id,
    )
    raise PostHogConfigurationError(
        f"PostHog project {project_id} configuration request failed"
    ) from None


def _safe_value(name: str, value: object, *, desired: bool = False) -> str:
    if desired:
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, int):
            return str(value)
        if name == "timezone":
            return "Asia/Shanghai"
        if name == "app_urls":
            return "[https://potatoma.me]"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if name == "timezone" and value in ("UTC", "Asia/Shanghai"):
        return str(value)
    if value == "<missing>":
        return "<missing>"
    return "<different>"


def _print_changes(project_id: int, changes: Mapping[str, tuple], apply: bool) -> None:
    mode = "apply" if apply else "check"
    print(f"PostHog project {project_id} {mode}:")
    if not changes:
        print("No field changes required.")
        return
    for name, (old, new) in changes.items():
        print(
            f"{name}: {_safe_value(name, old)} -> "
            f"{_safe_value(name, new, desired=True)}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check or apply approved PostHog project settings."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="show required changes without mutating PostHog (default)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="apply required changes and verify them with a second GET",
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except RuntimeError:
        print("PostHog project configuration unavailable.")
        return 2

    try:
        changes = configure_project(settings, apply=args.apply)
    except PostHogConfigurationError:
        print(f"PostHog project {settings.posthog_project_id}: configuration failed.")
        return 1
    _print_changes(settings.posthog_project_id, changes, args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
