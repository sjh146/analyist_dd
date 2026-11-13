"""Wall-clock sanity guard.

Why this exists: on 2026-09-10 the machine's clock ran ~4 hours off and later
jumped forward mid-session.  The loop believed the KST session was still open,
kept entering positions, and then executed its day-end logic at the wrong
moment.  Nothing in the stack noticed, because every component reads the same
skewed clock.

The guard compares the local clock against an external ``Date`` response header
(HTTP) and blocks **new entries** when the skew exceeds a tolerance.  It is
deliberately conservative in one direction only: a skewed clock stops trading,
while an unreachable network only warns (Creon market data is local, so the loop
can still trade correctly without internet -- see ``fail_closed`` to flip that).

Exits are never blocked: refusing to sell because a clock check failed would be
worse than selling on a slightly-wrong timestamp.
"""

from __future__ import annotations

import email.utils
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.request import Request, urlopen

DEFAULT_CLOCK_URL = "https://www.google.com"
DEFAULT_MAX_SKEW_SECONDS = 300.0
DEFAULT_CACHE_SECONDS = 600.0


@dataclass
class ClockStatus:
    """Result of one clock check."""

    reachable: bool
    skew_seconds: Optional[float]  # positive = local clock is ahead
    checked_at: Optional[datetime]
    source: str = ""
    error: str = ""


def parse_http_date(value: str) -> Optional[datetime]:
    """Parse an HTTP ``Date`` header into an aware UTC datetime."""
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_http_date(url: str, timeout: float = 8.0) -> datetime:
    """Return the server's ``Date`` header as an aware UTC datetime.

    Raises ``OSError``/``ValueError`` when unreachable or unparseable -- callers
    handle that as "cannot verify".
    """
    request = Request(url, method="HEAD", headers={"User-Agent": "trader-agent-clock/1"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        raw = response.headers.get("Date")
    parsed = parse_http_date(raw or "")
    if parsed is None:
        raise ValueError("no usable Date header from {0}".format(url))
    return parsed


def skew_seconds(source: datetime, local: Optional[datetime] = None) -> float:
    """Return ``local - source`` in seconds (positive = local clock ahead)."""
    local = local or datetime.now(timezone.utc)
    if local.tzinfo is None:
        local = local.replace(tzinfo=timezone.utc)
    return (local.astimezone(timezone.utc) - source.astimezone(timezone.utc)).total_seconds()


def blocks_entries(
    status: ClockStatus,
    max_skew_seconds: float = DEFAULT_MAX_SKEW_SECONDS,
    fail_closed: bool = False,
) -> bool:
    """Decide whether *status* forbids new entries.

    - measured skew beyond tolerance -> block (that is the whole point)
    - unreachable/unverifiable -> block only when ``fail_closed``
    """
    if status.checked_at is None:
        return bool(fail_closed)
    if status.skew_seconds is None:
        return bool(fail_closed)
    return abs(status.skew_seconds) > float(max_skew_seconds)


class ClockGuard:
    """Cached clock check with an explicit, testable policy."""

    def __init__(
        self,
        url: str = DEFAULT_CLOCK_URL,
        max_skew_seconds: float = DEFAULT_MAX_SKEW_SECONDS,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
        fail_closed: bool = False,
        timeout: float = 8.0,
        fetch: Optional[Callable[[str, float], datetime]] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.url = url
        self.max_skew_seconds = float(max_skew_seconds)
        self.cache_seconds = float(cache_seconds)
        self.fail_closed = bool(fail_closed)
        self.timeout = float(timeout)
        self._fetch = fetch or fetch_http_date
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._last: Optional[ClockStatus] = None
        self._last_monotonic: float = 0.0

    # -- internals ----------------------------------------------------- #
    def _fresh(self) -> bool:
        if self._last is None:
            return False
        if self.cache_seconds <= 0:
            return False
        return (time.monotonic() - self._last_monotonic) < self.cache_seconds

    def _run(self) -> ClockStatus:
        local = self._now()
        try:
            source = self._fetch(self.url, self.timeout)
        except Exception as exc:  # noqa: BLE001 - offline is a status, not a crash
            status = ClockStatus(
                reachable=False,
                skew_seconds=None,
                checked_at=None,
                source=self.url,
                error=str(exc)[:160],
            )
        else:
            status = ClockStatus(
                reachable=True,
                skew_seconds=skew_seconds(source, local),
                checked_at=local,
                source=self.url,
            )
        self._last = status
        self._last_monotonic = time.monotonic()
        return status

    # -- public API ---------------------------------------------------- #
    def check(self) -> ClockStatus:
        """Return the (cached) clock status."""
        if not self.url:
            # Disabled: an empty URL means "do not verify" (used by tests and by
            # offline runs), never "block everything".
            return ClockStatus(
                reachable=False, skew_seconds=None, checked_at=None,
                source="", error="clock check disabled",
            )
        if self._fresh():
            assert self._last is not None
            return self._last
        return self._run()

    def blocks(self) -> bool:
        if not self.url:
            return False
        return blocks_entries(self.check(), self.max_skew_seconds, self.fail_closed)

    def describe(self, status: Optional[ClockStatus] = None) -> str:
        """Human-readable one-liner for logs and reports."""
        if not self.url:
            return "clock check disabled"
        status = status or self.check()
        if status.skew_seconds is None:
            state = "UNVERIFIED" if status.reachable is False else "UNKNOWN"
            return "clock {0} ({1})".format(state, status.error or "no data")
        direction = "ahead of" if status.skew_seconds >= 0 else "behind"
        text = "clock {0:+.1f}s {1} server time ({2})".format(
            status.skew_seconds, direction, status.source
        )
        if abs(status.skew_seconds) > self.max_skew_seconds:
            text += " -> entries BLOCKED (limit {0:.0f}s)".format(self.max_skew_seconds)
        return text

    @classmethod
    def from_config(cls, config: Any) -> "ClockGuard":
        """Build a guard from a config object carrying the ``clock_*`` knobs."""
        return cls(
            url=getattr(config, "clock_check_url", DEFAULT_CLOCK_URL),
            max_skew_seconds=getattr(
                config, "clock_max_skew_seconds", DEFAULT_MAX_SKEW_SECONDS
            ),
            cache_seconds=getattr(config, "clock_check_interval_seconds", DEFAULT_CACHE_SECONDS),
            fail_closed=bool(getattr(config, "clock_check_fail_closed", False)),
            timeout=getattr(config, "clock_check_timeout_seconds", 8.0),
        )
