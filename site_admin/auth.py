import hmac
import logging
import math
from datetime import datetime, timezone
from threading import RLock


LOCK_SECONDS = 15 * 60
MAX_FAILURES = 5
MAX_TRACKED_IPS = 1_024


class LoginLimiter:
    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._lock = RLock()

    def allow(self, ip: str, now: float) -> tuple[bool, int]:
        with self._lock:
            self._prune_expired(now)
            failures = self._failures.get(ip, ())
            if len(failures) < MAX_FAILURES:
                return True, 0
            remaining = math.ceil(failures[0] + LOCK_SECONDS - now)
            return False, max(1, min(LOCK_SECONDS, remaining))

    def record_failure(self, ip: str, now: float) -> None:
        with self._lock:
            self._prune_expired(now)
            if ip not in self._failures and len(self._failures) >= MAX_TRACKED_IPS:
                if not self._evict_oldest_unlocked():
                    return
            self._failures.setdefault(ip, []).append(now)

    def clear(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)

    def _prune_expired(self, now: float) -> None:
        cutoff = now - LOCK_SECONDS
        for ip, recorded in list(self._failures.items()):
            current = [timestamp for timestamp in recorded if timestamp > cutoff]
            if current:
                self._failures[ip] = current
            else:
                self._failures.pop(ip, None)

    def _evict_oldest_unlocked(self) -> bool:
        candidates = (
            (max(failures), ip)
            for ip, failures in self._failures.items()
            if len(failures) < MAX_FAILURES
        )
        try:
            _, oldest_ip = min(candidates)
        except ValueError:
            return False
        self._failures.pop(oldest_ip)
        return True


def password_matches(submitted: str, configured: str) -> bool:
    return hmac.compare_digest(
        submitted.encode("utf-8"),
        configured.encode("utf-8"),
    )


def log_security_event(event: str, ip: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    logging.getLogger("site_admin.security").info(
        "event=%s ip=%s timestamp=%s",
        event,
        ip,
        timestamp,
    )
