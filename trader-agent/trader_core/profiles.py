"""Per-screener strategy profiles: the same candidate list is NOT one strategy.

``analysit_dd`` will deliver different kinds of candidate lists, and each needs
its own trading mechanic:

* **종가 스크리너 (``close``)** -- buy into the close (마감 동시호가 직전) and sell
  at the next session's open (다음 장 시가).  Overnight risk only; the trade has
  a *time* exit, not a stop.
* **스윙 스크리너 (``swing``)** -- hold for days with a stop/take-profit; the
  entry can happen any time during the session.
* **당일 (``daytrading``)** -- never held overnight: everything is closed before
  the bell.
* **rank_momentum** -- the self-generated Creon ranking source; stop/tp with an
  intraday entry window.

Feeding all of them through one rule set is how a close-to-open trade silently
becomes a week-long hold (or a swing name gets dumped at the open for no
reason), so the entry window and the exit mechanic are declared per screener and
enforced by the loop.

Pure logic only: no IO, no clock reads -- callers pass ``now`` in, which keeps
every rule unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Any, Dict, Optional, Tuple

from .config import KST

# Exit mechanics
EXIT_STOP_TP = "stop_tp"            # stop / take-profit, may hold for days
EXIT_NEXT_OPEN = "next_open"        # sell at the next session's open (종가 전략)
EXIT_SAME_DAY_CLOSE = "same_day_close"  # never overnight

_DEFAULT_WINDOW = "09:00-15:20"


def _parse_hhmm(text: str) -> Optional[dtime]:
    try:
        hour, _, minute = str(text).strip().partition(":")
        return dtime(int(hour), int(minute))
    except (TypeError, ValueError):
        return None


def parse_window(text: Any, default: str = _DEFAULT_WINDOW) -> Tuple[dtime, dtime]:
    """Parse ``"HH:MM-HH:MM"`` into ``(start, end)`` times (fallback: default)."""
    raw = str(text or default)
    start_text, _, end_text = raw.partition("-")
    start = _parse_hhmm(start_text) or _parse_hhmm(default.split("-")[0]) or dtime(9, 0)
    end = _parse_hhmm(end_text) or _parse_hhmm(default.split("-")[1]) or dtime(15, 20)
    return start, end


def _within(window: Tuple[dtime, dtime], clock: dtime) -> bool:
    start, end = window
    return start <= clock <= end


@dataclass
class ScreenerProfile:
    """How one screener's candidates are allowed to be traded."""

    name: str
    entry_window: str = _DEFAULT_WINDOW
    exit_mode: str = EXIT_STOP_TP
    hold_overnight: bool = True
    # Window in which a next_open / same_day_close exit is executed.
    open_exit_window: str = "09:00-09:10"
    close_exit_window: str = "15:10-15:25"
    # Time-based exits, on top of the stop/take-profit (the stop still governs
    # the downside; these only decide how long a *survivor* may occupy a slot).
    # A momentum entry has a shelf life: the ranking signal that produced it
    # decays, and a slot held by a dead position is a slot today's candidate
    # list cannot use -- measured 2026-09-11..22, 103 candidate-days were
    # rejected *only* by ``max_open_positions``.  Sessions count the entry
    # session as 1 (2 = "exit at the next session's close"), and every rule
    # fires only inside ``close_exit_window``.  0 disables each.
    max_hold_sessions: int = 0        # hard cap: close at the end of session N
    time_stop_sessions: int = 0       # momentum-decay cut at session N
    time_stop_gain: float = 0.01      # ... when the gain is below this
    flat_stop_sessions: int = 0       # dead-money release at session N
    flat_stop_band: float = 0.02      # ... when |gain| <= this band
    kelly_fraction: Optional[float] = None
    max_entries_per_day: Optional[int] = None
    enabled: bool = True
    extras: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    def entry_allowed(
        self, now: datetime, market_open: str = "09:00", market_close: str = "15:30"
    ) -> Tuple[bool, str]:
        """Return ``(allowed, reason)`` for opening a new position now."""
        if not self.enabled:
            return False, "{0}: profile disabled".format(self.name)
        clock = now.astimezone(KST).time() if now.tzinfo else now.time()
        # Market hours first: "market closed" is the more fundamental reason and
        # reads better in the loop log than "outside entry window".
        open_t, close_t = parse_window("{0}-{1}".format(market_open, market_close))
        if not _within((open_t, close_t), clock):
            return False, "{0}: market closed".format(self.name)
        start, end = parse_window(self.entry_window)
        if not _within((start, end), clock):
            return False, "{0}: outside entry window {1}".format(
                self.name, self.entry_window
            )
        return True, "ok"

    def time_exit_due(
        self, entry_ts: Optional[str], now: datetime
    ) -> Tuple[bool, str]:
        """Return ``(due, reason)`` for a time-based exit of an open trade.

        * ``next_open``: due on any later session, inside ``open_exit_window``.
        * ``same_day_close``: due the same day, inside ``close_exit_window``.
        * ``stop_tp``: never time-exits (None/empty reason).
        """
        if self.exit_mode == EXIT_STOP_TP:
            return False, ""
        moment = now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=KST)
        if self.exit_mode == EXIT_NEXT_OPEN:
            if not entry_ts:
                return False, ""
            if not _is_later_session(entry_ts, moment):
                return False, "next_open: same session, holding"
            if _within(parse_window(self.open_exit_window), moment.time()):
                return True, "next_open exit (종가 전략: 시가 매도)"
            return False, "next_open: waiting for the open window"
        if self.exit_mode == EXIT_SAME_DAY_CLOSE:
            if _within(parse_window(self.close_exit_window), moment.time()):
                return True, "same_day_close exit (당일 청산)"
            return False, "same_day_close: waiting for the close window"
        return False, ""

    def in_time_exit_window(self, now: datetime) -> bool:
        """True when a time-based exit may be executed at *now*."""
        moment = now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=KST)
        return _within(parse_window(self.close_exit_window), moment.time())

    def time_rules_due(
        self, entry_ts: Optional[str], now: datetime, gain_pct: Optional[float] = None
    ) -> Tuple[bool, str]:
        """Return ``(due, reason)`` for the time-exit ladder of a survivor.

        These rules are independent of ``exit_mode``: a ``stop_tp`` position
        (swing/momentum) is still governed by its stop and take-profit, but it
        may also be closed by *time* -- at the end of ``max_hold_sessions``
        whatever it did, when it is dead money (``flat_stop``), or when it
        never worked (``time_stop``).  ``gain_pct`` is the position's gain over
        its entry price; rules that need it fail open (hold) when it is
        ``None``, because a missing quote must never force a sale.
        """
        if not (self.max_hold_sessions or self.time_stop_sessions
                or self.flat_stop_sessions):
            return False, ""
        if not entry_ts:
            return False, ""
        if not self.in_time_exit_window(now):
            return False, ""
        sessions = sessions_held(entry_ts, now)
        if self.max_hold_sessions and sessions >= int(self.max_hold_sessions):
            return True, "max_hold_sessions exit (보유 상한 {0}일)".format(
                int(self.max_hold_sessions)
            )
        if self.flat_stop_sessions and sessions >= int(self.flat_stop_sessions):
            if gain_pct is not None and abs(float(gain_pct)) <= float(self.flat_stop_band):
                return True, "flat_stop exit (보합 청산 {0:+.2%} at {1}일)".format(
                    float(gain_pct), int(self.flat_stop_sessions)
                )
        if self.time_stop_sessions and sessions >= int(self.time_stop_sessions):
            if gain_pct is not None and float(gain_pct) < float(self.time_stop_gain):
                return True, "time_stop exit ({0:+.2%} < {1:+.2%} at {2}일)".format(
                    float(gain_pct), float(self.time_stop_gain),
                    int(self.time_stop_sessions),
                )
        return False, ""


def sessions_held(entry_ts: Optional[str], now: datetime) -> int:
    """Session count of an open trade, counting the entry session as 1.

    Business days only (weekends are skipped); exchange holidays are not
    modelled, which can only make a time exit fire *one session early*.
    Returns 0 when the entry timestamp is missing/unparseable -- callers treat
    0 as "no time rule can be due yet".
    """
    try:
        entry_day = datetime.strptime(str(entry_ts)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return 0
    moment = now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=KST)
    if moment.date() < entry_day:
        return 0
    count = 1
    cursor = entry_day
    while cursor < moment.date():
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return count


def _is_later_session(entry_ts: str, now: datetime) -> bool:
    """True when *now* falls on a KST day strictly after ``entry_ts``'s day."""
    text = str(entry_ts)[:10]
    try:
        entry_day = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return False
    return now.date() > entry_day


# Defaults per screener.  ``analysit_dd`` names (close/swing) plus the
# self-generated rank source and the daytrading list.
DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
    "close": {
        # Buy into the closing auction (through 15:29, i.e. the last minute the
        # closing auction accepts orders), sell at the next open.
        "entry_window": "14:50-15:29",
        "exit_mode": EXIT_NEXT_OPEN,
        "open_exit_window": "09:00-09:10",
        "hold_overnight": True,
        "kelly_fraction": 0.15,   # thinner edge, one night of gap risk
        "max_entries_per_day": 3,
    },
    "swing": {
        "entry_window": "09:00-15:20",
        "exit_mode": EXIT_STOP_TP,
        "hold_overnight": True,
        "kelly_fraction": 0.25,
        "max_entries_per_day": 3,
    },
    "daytrading": {
        # Never held overnight: in and out inside the session.
        "entry_window": "09:00-14:50",
        "exit_mode": EXIT_SAME_DAY_CLOSE,
        "close_exit_window": "15:00-15:25",
        "hold_overnight": False,
        "kelly_fraction": 0.15,
        "max_entries_per_day": 3,
    },
    "rank_momentum": {
        "entry_window": "09:00-15:20",
        "exit_mode": EXIT_STOP_TP,
        "hold_overnight": True,
        "kelly_fraction": 0.25,
        "max_entries_per_day": 3,
        # The 상승률 ranking signal decays, so a survivor that has gone nowhere
        # must not keep occupying one of the 10 slots.  Chosen 2026-09-22 from
        # a 1-minute replay of the trades inside Naver's minute window (7
        # closed + 10 open, entries/quantities fixed):
        #   * flat_stop 2세션 ±2%  : closed +17,890 (vs +17,890 current) and the
        #     open book -15,090 (vs -24,250 holding them) -- the only rule that
        #     improved the marked book with no cost on the realised trades.
        #   * time_stop 2세션 +1%  : closed +17,695, open -25,910 -> keep 0 (off).
        #     Cutting every under-performer at day 2 also cuts the names that
        #     then run (468530 was -3.4% at its first close, +33% at its fifth).
        #   * max_hold 5세션       : closed +18,870, open -27,340 (net ~ -2.1k
        #     on 17 trades) -- kept as the hard tail bound; nothing may sit for
        #     a week.  0 disables it if the tail is ever wanted back.
        # Tightening the stop (6% cap: open book -35,520) or the trail
        # (arm2/trail2: closed -290) measured worse; the vol-scaled stop and
        # the 5.5%/5% trail stay as they are.
        "flat_stop_sessions": 2,
        "flat_stop_band": 0.02,
        "max_hold_sessions": 5,
        "time_stop_sessions": 0,
    },
}


def profile_for(
    name: str, overrides: Optional[Dict[str, Dict[str, Any]]] = None
) -> ScreenerProfile:
    """Return the profile for *name* (default + optional user overrides).

    Unknown screeners inherit the ``swing`` shape (hold with stop/tp) but keep
    their own name, so a new ``analysit_dd`` list is never traded with the wrong
    exit mechanic by accident -- it behaves like a swing list until a profile is
    declared for it.
    """
    base = dict(DEFAULT_PROFILES.get(name) or DEFAULT_PROFILES["swing"])
    if overrides and isinstance(overrides.get(name), dict):
        base.update({k: v for k, v in overrides[name].items()})
    known = set(ScreenerProfile.__dataclass_fields__) - {"name", "extras"}
    extras = {k: v for k, v in base.items() if k not in known}
    kwargs = {k: v for k, v in base.items() if k in known}
    return ScreenerProfile(name=name, extras=extras, **kwargs)
