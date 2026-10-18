import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    admin_password: str
    session_secret: str
    posthog_personal_key: str
    posthog_project_id: int
    posthog_organization_id: str = "00000000-0000-0000-0000-000000000000"
    posthog_api_host: str = "https://us.posthog.com"
    url_prefix: str = "/site-admin"

    @classmethod
    def from_env(cls) -> "Settings":
        env_path = Path(__file__).resolve().parent / ".env"
        load_dotenv(env_path)
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "Settings":
        names = (
            "SITE_ADMIN_PASSWORD",
            "SITE_ADMIN_SESSION_SECRET",
            "POSTHOG_PERSONAL_API_KEY",
            "POSTHOG_PROJECT_ID",
        )
        values = {name: mapping.get(name, "").strip() for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError("Missing configuration: " + ", ".join(missing))
        if len(values["SITE_ADMIN_SESSION_SECRET"]) < 32:
            raise RuntimeError("SITE_ADMIN_SESSION_SECRET must be at least 32 characters")
        try:
            project_id = int(values["POSTHOG_PROJECT_ID"])
        except ValueError as exc:
            raise RuntimeError("POSTHOG_PROJECT_ID must be numeric") from exc
        configured_host = mapping.get("POSTHOG_API_HOST", "https://us.posthog.com")
        if not isinstance(configured_host, str):
            raise RuntimeError("POSTHOG_API_HOST must be exactly https://us.posthog.com")
        parsed_host = urlsplit(configured_host)
        if parsed_host.scheme.lower() != "https":
            raise RuntimeError("POSTHOG_API_HOST must use HTTPS")
        try:
            port = parsed_host.port
        except ValueError:
            port = -1
        if (
            parsed_host.hostname != "us.posthog.com"
            or parsed_host.username is not None
            or parsed_host.password is not None
            or port is not None
            or parsed_host.path != ""
            or parsed_host.query != ""
            or parsed_host.fragment != ""
        ):
            raise RuntimeError(
                "POSTHOG_API_HOST must be exactly https://us.posthog.com"
            )
        host = "https://us.posthog.com"
        return cls(
            admin_password=values["SITE_ADMIN_PASSWORD"],
            session_secret=values["SITE_ADMIN_SESSION_SECRET"],
            posthog_personal_key=values["POSTHOG_PERSONAL_API_KEY"],
            posthog_project_id=project_id,
            posthog_organization_id=mapping.get(
                "POSTHOG_ORGANIZATION_ID",
                "00000000-0000-0000-0000-000000000000",
            ),
            posthog_api_host=host,
        )
