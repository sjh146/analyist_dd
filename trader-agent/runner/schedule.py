"""Cycle cadence helpers: base interval + intraday focus windows.

The loop's job is to be *cheap* when nothing is happening and *fast* when it
matters.  The market's attention is concentrated at the open, in the early
afternoon and into the closing auction, so those windows get a shorter cycle
interval while the rest of the session keeps the slow base cadence (each cycle
costs broker requests, and Daishin blocks clients that over-ask).

Pure functions: callers pass ``now`` in, so the schedule is unit-testable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from trader_core.config import KST
from trader_core.profiles import parse_window

# window -> shorter interval while it is active
DEFAULT_FOCUS_WINDOWS: List[Dict[str, Any]] = [
    {"window": "09:00-09:30", "interval_seconds": 60},   # 장초 집중
    {"window": "13:00-14:00", "interval_seconds": 60},   # 오후 집중
    # Closing hour: the trailing stop and the 종가 entry both live here, and a
    # 5-minute gap can give back half of a 5 % trail, so watch every 30 s.
    {"window": "14:30-15:30", "interval_seconds": 30},
]


def _clock(now: datetime):
    return now.astimezone(KST).time() if now.tzinfo else now.time()


def active_focus(
    now: datetime, focus_windows: Optional[List[Dict[str, Any]]] = None
) -> Optional[Dict[str, Any]]:
    """Return the focus window covering *now* (or None).

    The shortest ``interval_seconds`` wins when windows overlap.
    """
    windows = focus_windows if focus_windows is not None else DEFAULT_FOCUS_WINDOWS
    clock = _clock(now)
    best: Optional[Dict[str, Any]] = None
    for entry in windows or []:
        if not isinstance(entry, dict):
            continue
        start, end = parse_window(entry.get("window"), "09:00-09:30")
        if not (start <= clock <= end):
            continue
        try:
            interval = float(entry.get("interval_seconds") or 0)
        except (TypeError, ValueError):
            continue
        if interval <= 0:
            continue
        if best is None or interval < float(best.get("interval_seconds") or 0):
            best = {"window": entry.get("window"), "interval_seconds": interval}
    return best


def cycle_interval(
    now: datetime,
    base_interval: float,
    focus_windows: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[float, Optional[Dict[str, Any]]]:
    """Return ``(interval_seconds, focus)`` to use for the sleep after *now*.

    ``focus`` describes the window that shortened the cadence (None when the
    base interval applies).
    """
    base = float(base_interval or 0)
    focus = active_focus(now, focus_windows)
    if focus is None:
        return base, None
    interval = min(base, float(focus["interval_seconds"]))
    return interval, focus
