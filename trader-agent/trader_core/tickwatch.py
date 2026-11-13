"""Sub-cycle exit watch: decide exits from the bridge's *realtime cache*.

The loop evaluates exit rules once per cycle (30 s in the focus windows, 300 s
otherwise).  Between cycles it can watch the StockCur ticks the bridge already
holds -- ``GET /quotes`` with no codes returns ``RealtimeHub.latest_quotes()``
and costs **no Daishin request** -- and flag a position whose cached price has
crossed its own exit line.  That is what turns "reaction time = one cycle" into
"reaction time = one tick poll" without spending the per-second request budget,
which is the reason the cadence is slow in the first place.

Two rules keep this honest, both from measured incidents:

* **A cached tick is not a price source on its own.**  The cache once held 6,050
  for a name the broker marked at 5,040, so a -17.8 % holding sat un-stopped.
  Every breach found here is a *candidate*: the caller must confirm it against
  the broker mark (``eval_amount / qty`` from ``/positions``) and believe the
  worse (lower) price before sending an order.
* **A stale tick is not a price either.**  Ticks older than ``max_age_seconds``
  are ignored and a missing tick leaves the position untouched (fail-open,
  hold) -- a data gap must never force a sale.

Pure logic: no IO, no clock reads (``now`` is passed in), no journal writes.
The high-water mark is *read* from the journal row but never written here; the
regular monitor cycle keeps it, and the live tick only makes the peak more
conservative (a higher peak can only arm the trail earlier, never later).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class TickBreach:
    """A cached tick that crossed one position's own exit line."""

    code: str
    reason: str
    price: float
    qty: int
    tick_age: float


def plain_code(value: Any) -> Optional[str]:
    """Normalise a position/quote code to the plain 6-digit form (or None)."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if text.startswith("A") and len(text) == 7 and text[1:].isdigit():
        return text[1:]
    if len(text) == 6 and text.isdigit():
        return text
    return None


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def tick_age(tick: Dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    """Seconds since the tick was produced (or None when it has no timestamp)."""
    stamp = _num(tick.get("ts"))
    if stamp is None or stamp <= 0:
        return None
    reference = now.timestamp() if isinstance(now, datetime) else None
    if reference is None:
        reference = time.time()
    age = reference - stamp
    return age if age >= 0 else 0.0


def tick_exit_candidates(
    positions: Iterable[Dict[str, Any]],
    rows: Iterable[Dict[str, Any]],
    ticks: Dict[str, Dict[str, Any]],
    now: Optional[datetime] = None,
    max_age_seconds: float = 5.0,
    default_stop_pct: float = 0.0,
    default_tp_pct: float = 0.0,
    arm_pct: float = 0.0,
    trail_pct: float = 0.0,
) -> List[TickBreach]:
    """Return breaches found in *ticks* for the open trade rows in *rows*.

    ``rows`` are journal trade rows (``code``/``price``/``stop_pct``/``tp_pct``/
    ``hwm_price``), ``positions`` the broker's ``/positions`` snapshot (for the
    sellable quantity and the code normalisation) and ``ticks`` the bridge's
    realtime cache.  Levels recorded on the row win; ``default_*`` fills the
    gaps, matching ``PositionMonitor._levels``.
    """
    rows_by_code: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        code = plain_code(row.get("code"))
        if code:
            rows_by_code[code] = dict(row)

    out: List[TickBreach] = []
    seen: set = set()
    for position in positions or []:
        code = plain_code(position.get("code"))
        if not code or code in seen:
            continue
        seen.add(code)
        row = rows_by_code.get(code)
        if row is None:
            continue
        qty = _int(position.get("sellable_qty", position.get("qty", 0)))
        if qty <= 0:
            continue
        tick = (ticks or {}).get(code)
        if not isinstance(tick, dict):
            continue
        price = _num(tick.get("current"))
        if price is None or price <= 0:
            continue
        age = tick_age(tick, now)
        if age is None or age > float(max_age_seconds):
            continue
        entry = _num(row.get("price"))
        if entry is None or entry <= 0:
            continue

        stop_pct = _num(row.get("stop_pct"))
        if stop_pct is None:
            stop_pct = _num(default_stop_pct) or 0.0
        tp_pct = _num(row.get("tp_pct"))
        if tp_pct is None:
            tp_pct = _num(default_tp_pct) or 0.0

        reason = ""
        if stop_pct > 0 and price <= entry * (1.0 - stop_pct):
            reason = "stop"
        elif tp_pct > 0 and price >= entry * (1.0 + tp_pct):
            reason = "tp"
        elif trail_pct > 0:
            stored = _num(row.get("hwm_price")) or entry
            peak = max(stored, price)
            if peak >= entry * (1.0 + float(arm_pct)) and price <= peak * (1.0 - float(trail_pct)):
                reason = "trailing stop (-{0:.0%} from peak {1:,.0f})".format(
                    float(trail_pct), peak
                )
        if reason:
            out.append(
                TickBreach(code=code, reason=reason, price=price, qty=qty, tick_age=age)
            )
    return out
