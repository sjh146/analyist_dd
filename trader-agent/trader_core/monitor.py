"""PositionMonitor: stop-loss / take-profit scanning over live positions.

On every ``heartbeat()`` the monitor pulls ``/positions`` and ``/quotes`` and
evaluates each open position against its stop/take-profit levels.  A position
whose quote is missing is treated as HOLD (never fabricated) and reported in
the status notes so an operator can see why nothing fired.

Exit signals are market sells (liquidations take priority over new entries and
are routed straight through ``TraderEngine.execute``, not through the entry
gate pipeline).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import risk
from .config import Config, KST


@dataclass
class ExitSignal:
    """A sell-to-close recommendation from the monitor."""

    code: str
    side: str = "sell"
    reason: str = "stop"
    qty: int = 0
    order_type: str = "market"
    price: Optional[float] = None
    name: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "side": self.side,
            "reason": self.reason,
            "qty": self.qty,
            "order_type": self.order_type,
            "price": self.price,
            "name": self.name,
        }


@dataclass
class MonitorReport:
    """The outcome of one monitor heartbeat."""

    ts: datetime
    exits: List[ExitSignal] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


class PositionMonitor:
    """Evaluates open positions for stop/take-profit exits."""

    def __init__(
        self,
        bridge: Any,
        config: Optional[Config] = None,
        journal: Any = None,
        profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        """Wire the monitor to a bridge, config and optional journal.

        ``bridge`` exposes the :class:`BridgeClient` surface (or a
        :class:`MockBridge`); ``journal`` is used to look up per-trade stop /
        take-profit levels set at entry time.  ``profiles`` are the per-screener
        strategy profiles: they decide whether a position is closed by *time*
        (종가 전략: sold at the next open) instead of by stop/take-profit.
        """
        self.bridge = bridge
        self.config = config if config is not None else Config()
        self.journal = journal
        self.profiles = profiles or {}

    def heartbeat(
        self, now: Optional[datetime] = None
    ) -> MonitorReport:
        """Pull positions + quotes and return exit signals and status notes."""
        now = now if now is not None else datetime.now(KST)
        positions = self.bridge.positions()
        exits: List[ExitSignal] = []
        notes: List[str] = []

        if not positions:
            return MonitorReport(ts=now, exits=exits, notes=["no open positions"])

        codes = [_code_of(pos) for pos in positions]
        codes = [c for c in codes if c]
        quotes = {}
        if codes:
            try:
                quotes = self.bridge.quotes(codes)
            except Exception as exc:  # noqa: BLE001 - monitor must not die
                notes.append("quote fetch failed: {0}".format(exc))

        for position in positions:
            code = _code_of(position)
            if not code:
                notes.append("position without a usable code skipped")
                continue
            entry = _as_float(position.get("avg_price"))
            qty = _as_int(position.get("sellable_qty", position.get("qty", 0)))
            if entry is None or entry <= 0:
                notes.append("{0}: no entry price; holding".format(code))
                continue
            if qty <= 0:
                notes.append("{0}: no sellable quantity; holding".format(code))
                continue
            quote = quotes.get(code, {})
            current = quote.get("current") if isinstance(quote, dict) else None
            current = _as_float(current)
            if current is None or current <= 0:
                notes.append("{0}: no current quote; holding".format(code))
                continue

            # Cross-check the live quote against the broker's own mark
            # (``eval_amount / qty`` from the same /positions row).  A wide gap
            # means one of the two feeds is wrong; for a long position the
            # *lower* price is the conservative one, so a stop still fires.
            current, mark_note = self._reconcile_price(position, current)
            if mark_note:
                notes.append(mark_note)

            stop_pct, tp_pct = self._levels(code)
            verdict = risk.should_stop(
                position, current, sl_pct=stop_pct, tp_pct=tp_pct
            )
            trailing = self._trailing_verdict(code, entry, current)
            if verdict == risk.STOP:
                exits.append(
                    ExitSignal(
                        code=code,
                        reason="stop",
                        qty=qty,
                        price=current,
                        name=str(position.get("name", "") or ""),
                    )
                )
            elif verdict == risk.TAKE_PROFIT:
                exits.append(
                    ExitSignal(
                        code=code,
                        reason="tp",
                        qty=qty,
                        price=current,
                        name=str(position.get("name", "") or ""),
                    )
                )
            elif trailing is not None:
                exits.append(
                    ExitSignal(
                        code=code,
                        reason=trailing,
                        qty=qty,
                        price=current,
                        name=str(position.get("name", "") or ""),
                    )
                )
            else:
                notes.append(
                    "{0}: holding (px {1} vs entry {2})".format(
                        code, current, entry
                    )
                )

        self._add_time_exits(positions, quotes, exits, notes, now)
        return MonitorReport(ts=now, exits=exits, notes=notes)

    def _reconcile_price(
        self, position: Dict[str, Any], quote_price: float
    ) -> tuple:
        """Return ``(price_for_decisions, note)`` using the broker's own mark.

        ``/positions`` carries ``eval_amount``, i.e. the broker's mark for this
        holding, so ``eval_amount / qty`` is a second, independent price.  When
        the two disagree beyond ``quote_divergence_pct`` we take the **lower**
        price (conservative for a long: stops still fire, take-profits do not
        fire early) and say so in the notes.  A missing/unusable mark leaves the
        quote untouched -- this is a cross-check, not a gate.
        """
        threshold = float(getattr(self.config, "quote_divergence_pct", 0.0) or 0.0)
        if threshold <= 0:
            return quote_price, ""
        qty = _as_int(position.get("qty", 0))
        eval_amount = _as_float(position.get("eval_amount"))
        if qty <= 0 or eval_amount is None or eval_amount <= 0:
            return quote_price, ""
        mark = eval_amount / float(qty)
        if mark <= 0:
            return quote_price, ""
        divergence = abs(quote_price - mark) / mark
        if divergence <= threshold:
            return quote_price, ""
        conservative = min(quote_price, mark)
        return conservative, "{0}: quote {1:,.0f} vs broker mark {2:,.0f} ({3:+.1%}) -> using {4:,.0f}".format(
            _code_of(position), quote_price, mark, divergence, conservative
        )

    def _trailing_verdict(
        self, code: str, entry: float, current: float
    ) -> Optional[str]:
        """Return a trailing-stop reason when *code* gave back its peak.

        The high-water mark lives on the journal row so it survives loop
        restarts.  The trail only *arms* once the position is up
        ``trailing_arm_pct``; before that the fixed stop governs, so nothing
        changes for a trade that never went anywhere.
        """
        trail = float(getattr(self.config, "trailing_stop_pct", 0.0) or 0.0)
        arm = float(getattr(self.config, "trailing_arm_pct", 0.0) or 0.0)
        if trail <= 0 or self.journal is None:
            return None
        try:
            row = self.journal.open_trade_for(code)
            if row is None:
                return None
            stored = _as_float(row.get("hwm_price")) or 0.0
            peak = max(stored, current)
            if peak > stored:
                self.journal.update_hwm(row["id"], peak)
            if peak < entry * (1.0 + arm):
                return None  # not armed yet: the fixed stop still governs
            if current <= peak * (1.0 - trail):
                return "trailing stop (-{0:.0%} from peak {1:,.0f})".format(
                    trail, peak
                )
        except Exception:  # noqa: BLE001 - a monitor error must never wedge exits
            return None
        return None

    def _add_time_exits(
        self,
        positions: List[Dict[str, Any]],
        quotes: Dict[str, Dict[str, Any]],
        exits: List[ExitSignal],
        notes: List[str],
        now: datetime,
    ) -> None:
        """Append exits whose *profile* closes them by time, not by price.

        Two families live here.  A 종가 (``close``) position is bought into the
        closing auction and sold at the next session's open, so it must exit
        even though no stop or take-profit was touched -- the screener stored
        on the journal row decides that.  A momentum (``rank_momentum``)
        position is held by stop/take-profit but still has a *shelf life*:
        ``time_rules_due`` releases dead money (``flat_stop``) and bounds the
        hold (``max_hold_sessions``) so a slot comes back to the day's list.
        """
        from .profiles import profile_for  # local import: keeps imports acyclic

        if self.journal is None:
            return
        already = {signal.code for signal in exits}
        for position in positions:
            code = _code_of(position)
            if not code or code in already:
                continue
            try:
                row = self.journal.open_trade_for(code)
            except Exception:  # noqa: BLE001 - journal problems must not wedge us
                continue
            if row is None:
                continue
            profile = profile_for(str(row.get("screener") or ""), self.profiles)
            due, reason = profile.time_exit_due(row.get("ts"), now)
            if not due:
                due, reason = profile.time_rules_due(
                    row.get("ts"), now, self._gain_pct(row, quotes.get(code))
                )
            if not due:
                continue
            qty = _as_int(position.get("sellable_qty", position.get("qty", 0)))
            if qty <= 0:
                continue
            quote = quotes.get(code) or {}
            current = _as_float(quote.get("current")) if isinstance(quote, dict) else None
            exits.append(
                ExitSignal(
                    code=code,
                    reason=reason,
                    qty=qty,
                    price=current,
                    name=str(position.get("name", "") or ""),
                )
            )
            notes.append("{0}: {1}".format(code, reason))

    @staticmethod
    def _gain_pct(row: Dict[str, Any], quote: Any) -> Optional[float]:
        """Return a position's gain over its *journal* entry price, or None.

        ``None`` (missing quote/entry) makes the gain-dependent time rules fail
        open: a data gap must never force a sale.
        """
        entry = _as_float(row.get("price"))
        current = _as_float(quote.get("current")) if isinstance(quote, dict) else None
        if not entry or entry <= 0 or not current or current <= 0:
            return None
        return current / entry - 1.0

    def _levels(self, code: str) -> tuple:
        """Return ``(stop_pct, tp_pct)`` for a code from journal or defaults."""
        if self.journal is not None:
            try:
                row = self.journal.open_trade_for(code)
            except Exception:  # noqa: BLE001 - journal problems fall back
                row = None
            if row is not None:
                stop = _as_float(row.get("stop_pct"))
                tp = _as_float(row.get("tp_pct"))
                if stop is not None and tp is not None:
                    return stop, tp
        return self.config.stop_loss_pct, self.config.take_profit_pct


def _code_of(position: Dict[str, Any]) -> Optional[str]:
    value = position.get("code")
    if value is None:
        return None
    text = str(value).strip().upper()
    if text.startswith("A") and len(text) == 7 and text[1:].isdigit():
        return text[1:]
    if len(text) == 6 and text.isdigit():
        return text
    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0
