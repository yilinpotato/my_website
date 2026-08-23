from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".c",
    ".cpp",
    ".css",
    ".h",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        text=False,
    )
    return [ROOT / item.decode("utf-8") for item in output.split(b"\0") if item]


def main() -> int:
    api_key_prefix = "AI" + "za"
    rules = {
        "google_api_key": re.compile(api_key_prefix + r"[0-9A-Za-z_-]{20,}"),
        "hardcoded_sensitive_assignment": re.compile(
            r"(?i)\b(?:GEMINI_API_KEY|MAIL_USERNAME|MAIL_PASSWORD|ADMIN_PASSWORD|"
            r"ADMIN_IP|MY_COOKIE_STRING)\s*=\s*['\"][^'\"\r\n]{3,}['\"]"
        ),
        "hardcoded_flask_config": re.compile(
            r"(?i)app\.config\[['\"](?:SECRET_KEY|MAIL_USERNAME|MAIL_PASSWORD)['\"]\]"
            r"\s*=\s*['\"][^'\"\r\n]{3,}['\"]"
        ),
        "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    }

    findings: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("new/myproject/secure_uploads/") and not relative.endswith("/.gitkeep"):
            findings.append(f"tracked_upload:{relative}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule_name, pattern in rules.items():
                if pattern.search(line):
                    findings.append(f"{rule_name}:{relative}:{line_number}")

    if findings:
        print("Security scan failed; sensitive tracked content remains:")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print("Security scan passed: no forbidden tracked secrets or uploads found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
