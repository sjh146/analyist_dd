"""Algorithm attribution for every trade.

Requirement: the journal (and the running log) must say *which algorithm* made a
trade, so later reviews can compare "algorithm A vs B" instead of re-deriving
intent from numbers.  Example record::

    {"version": "2026-09-10",
     "screener": "rank_momentum",
     "scoring": "percentile",
     "filters": {"min_pct": 2.0, "max_pct": 12.0, "min_pullback_pct": 3.0, ...},
     "candidate": {"rank": 5, "pct": 27.5, "volume": 1920258, "score": 93.1,
                   "day_range_pct": 23.8, "off_high_pct": -1.9},
     "sizing": {"model": "kelly+risk-parity", "kelly_f": 0.06, "kelly_qty": 15,
                "risk_qty": 41, "final_qty": 15, "risk_per_trade_pct": 0.005},
     "stop": {"model": "vol-range", "stop_pct": 0.12, "tp_pct": 0.2571,
              "trailing_stop_pct": 0.05, "trailing_arm_pct": 0.03},
     "exit_mode": "stop_tp"}

**Latency rule: this module is pure in-memory dict/string work.**  It must never
add a broker request or a sleep -- the numbers all arrive as locals that the
engine already computed.  Attribution is written once per order, not per cycle.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

# Bump when the *decision* logic changes so trades can be grouped by algorithm
# generation.  The individual tags below (scoring/stop/sizing models) are the
# fine-grained record.
DECISION_VERSION = "2026-09-10"

KEYS = (
    "version", "screener", "scoring", "filters", "candidate",
    "sizing", "stop", "exit_mode", "side",
)


def _num(value: Any, digits: int = 4) -> Optional[float]:
    """Return a JSON-safe float (or None) rounded for readability."""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def entry_record(
    screener: str,
    scoring: str,
    params: Optional[Dict[str, Any]],
    candidate: Dict[str, Any],
    sizing: Dict[str, Any],
    stop: Dict[str, Any],
    exit_mode: str = "",
) -> Dict[str, Any]:
    """Build the attribution record for a *buy* plan.

    ``params`` are the feed's filter params, ``candidate`` the per-name metrics,
    ``sizing`` the chosen size path and ``stop`` the exit-level model.  Every
    field is optional; whatever is missing is simply omitted from the record.
    """
    params = params or {}
    filters = {
        key: _num(params.get(key), 6) if isinstance(params.get(key), (int, float)) else params.get(key)
        for key in (
            "min_pct", "max_pct", "min_pullback_pct", "min_volume",
            "skip_limit_up", "top_n",
        )
        if key in params
    }
    return {
        "version": DECISION_VERSION,
        "screener": screener,
        "scoring": scoring or "unknown",
        "filters": filters,
        "candidate": {key: _num(value, 4) for key, value in candidate.items()
                      if value is not None},
        "sizing": dict(sizing or {}),
        "stop": dict(stop or {}),
        "exit_mode": exit_mode or "",
        "side": "buy",
    }


def exit_record(
    rule: str,
    trigger_price: Optional[float] = None,
    price_source: str = "",
    qty: Optional[int] = None,
    peak: Optional[float] = None,
) -> Dict[str, Any]:
    """Attribution for a *sell*: which rule fired, on which price."""
    record: Dict[str, Any] = {
        "version": DECISION_VERSION,
        "rule": rule or "unspecified",
        "side": "sell",
    }
    if trigger_price is not None:
        record["trigger_price"] = _num(trigger_price, 4)
    if price_source:
        record["price_source"] = price_source
    if qty is not None:
        record["qty"] = int(qty)
    if peak is not None:
        record["peak"] = _num(peak, 4)
    return record


def dumps(record: Optional[Dict[str, Any]]) -> str:
    """Serialize a record for the journal column (never raises)."""
    if not record:
        return "{}"
    try:
        return json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return "{}"


def loads(raw: Any) -> Dict[str, Any]:
    """Parse a stored record back into a dict (never raises)."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def merge(raw: Any, patch: Optional[Dict[str, Any]]) -> str:
    """Merge *patch* into a stored record (used when a position closes)."""
    record = loads(raw)
    if patch:
        record.update(patch)
    return dumps(record)


def summary(record: Optional[Dict[str, Any]]) -> str:
    """One-line human summary for the running log (ASCII-safe for cp949)."""
    record = record or {}
    if record.get("side") == "sell" or record.get("rule"):
        return "exit_rule={0} src={1} px={2}".format(
            record.get("rule", "?"),
            record.get("price_source", "?"),
            record.get("trigger_price", "?"),
        )
    candidate = record.get("candidate") or {}
    sizing = record.get("sizing") or {}
    stop = record.get("stop") or {}
    parts = [
        "screener={0}".format(record.get("screener", "?")),
        "scoring={0}".format(record.get("scoring", "?")),
    ]
    if candidate:
        parts.append(
            "rank={0} pct={1} score={2}".format(
                candidate.get("rank"), candidate.get("pct"), candidate.get("score")
            )
        )
        if candidate.get("day_range_pct") is not None:
            parts.append("range={0}%".format(candidate.get("day_range_pct")))
        if candidate.get("off_high_pct") is not None:
            parts.append("off_high={0}%".format(candidate.get("off_high_pct")))
    if sizing:
        parts.append(
            "size={0} qty={1} (kelly {2}/risk {3})".format(
                sizing.get("model"), sizing.get("final_qty"),
                sizing.get("kelly_qty"), sizing.get("risk_qty"),
            )
        )
    if stop:
        parts.append(
            "stop={0} {1} tp={2}".format(
                stop.get("model"), stop.get("stop_pct"), stop.get("tp_pct")
            )
        )
    return " ".join(str(part) for part in parts)
