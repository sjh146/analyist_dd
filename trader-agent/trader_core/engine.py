"""TraderEngine: the gate pipeline between signal and order.

Every candidate from the screener feed is pushed through the same ordered
gate list (documented in PHASE3_BRIEF.md).  Only when EVERY gate passes does
the :class:`Decision` carry an :class:`OrderPlan`, sized with fractional Kelly
from account equity and the screener win-rate stats (falling back to config
priors when the feed stats are empty).

Gate order (canonical, per decision):

1. ``feed_fresh``        - payload generated_at within ``feed_max_age``
2. ``kill_switch``       - no kill-switch file present
3. ``market_hours``      - KST Mon-Fri 09:00-15:30 (unless disabled);
                           daytrading always needs a live session
4. ``daily_trade_count`` - today's executions < ``daily_max_trades``
5. ``balance_position_amount`` - cash, per-stock concentration, per-order
                           KRW, per-path slot (swing 3 / daytrading 2) and
                           global concurrent-position caps all pass
6. ``signal_sane``       - score > ``min_score`` and price > 0

``execute(plan)`` routes a plan through the bridge (or a mock/dry-run
executor) and journals the fill.  Market orders are refused unless a recent
reference quote exists (CWE-20 guard).  ``liquidation_plans`` converts open
positions into sell plans for the kill-switch / close-all path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Dict, List, Optional

from . import algo, risk, slot_rules
from .bridge_client import BridgeClient, BridgeError
from .config import Config, KST
from .feed import Candidate, FeedSnapshot, candidate_change_pct
from .journal import Journal
from .killswitch import kill_switch_active, kill_switch_reason

GATES = (
    "feed_fresh",
    "kill_switch",
    "market_hours",
    "daily_trade_count",
    "balance_position_amount",
    "signal_sane",
)


class OrderBlockedError(Exception):
    """A valid plan could not be executed (gate/CWE-20/liquidity refusal)."""


@dataclass
class GateResult:
    """The verdict of a single gate for one candidate."""

    gate: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"gate": self.gate, "ok": self.ok, "detail": self.detail}


@dataclass
class OrderPlan:
    """A fully gated order ready for the bridge."""

    side: str
    code: str
    qty: int
    price: Optional[float] = None
    order_type: str = "limit"
    entry_reason: str = ""
    screener: str = ""
    name: str = ""
    kelly_f: Optional[float] = None
    stop_pct: Optional[float] = None
    tp_pct: Optional[float] = None
    # Which algorithm produced this order (see trader_core.algo): screener,
    # scoring mode, filters, candidate metrics, sizing path, stop model.  The
    # loop enriches it with the screener profile, then the journal stores it.
    algo: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "side": self.side,
            "code": self.code,
            "qty": self.qty,
            "price": self.price,
            "order_type": self.order_type,
            "entry_reason": self.entry_reason,
            "screener": self.screener,
            "name": self.name,
            "kelly_f": self.kelly_f,
            "stop_pct": self.stop_pct,
            "tp_pct": self.tp_pct,
        }


@dataclass
class Decision:
    """Engine verdict for one screener candidate."""

    screener: str
    code: str
    name: str
    score: Optional[float]
    price: Optional[float]
    reason: str
    gates: List[GateResult] = field(default_factory=list)
    plan: Optional[OrderPlan] = None

    @property
    def passed(self) -> bool:
        return self.plan is not None

    def blocked_reason(self) -> Optional[str]:
        """Return the first failing gate detail (or None when passed)."""
        for gate in self.gates:
            if not gate.ok:
                return "{0}: {1}".format(gate.gate, gate.detail)
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "screener": self.screener,
            "code": self.code,
            "name": self.name,
            "score": self.score,
            "price": self.price,
            "reason": self.reason,
            "passed": self.passed,
            "gates": [g.as_dict() for g in self.gates],
            "blocked_reason": self.blocked_reason(),
            "plan": self.plan.as_dict() if self.plan else None,
        }


@dataclass
class DecisionReport:
    """A whole-scan verdict across every screener."""

    generated_at: Optional[datetime] = None
    feed_fresh: bool = False
    feed_error: Optional[str] = None
    market_open: bool = False
    kill_switch: bool = False
    kill_switch_reason: str = ""
    account: Optional["AccountState"] = None
    decisions: List[Decision] = field(default_factory=list)

    @property
    def actionable(self) -> List[Decision]:
        """Decisions that carry a ready-to-send :class:`OrderPlan`."""
        return [d for d in self.decisions if d.passed]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "feed_fresh": self.feed_fresh,
            "feed_error": self.feed_error,
            "market_open": self.market_open,
            "kill_switch": self.kill_switch,
            "kill_switch_reason": self.kill_switch_reason,
            "total_decisions": len(self.decisions),
            "actionable": len(self.actionable),
            "decisions": [d.as_dict() for d in self.decisions],
        }


@dataclass
class AccountState:
    """Account snapshot used by the gates (cash, equity, holdings, quotes)."""

    cash: float = 0.0
    equity: float = 0.0
    positions: List[Dict[str, Any]] = field(default_factory=list)
    open_orders: List[Dict[str, Any]] = field(default_factory=list)
    quotes: Dict[str, float] = field(default_factory=dict)
    # ``cash`` 가 브로커에서 실제로 온 값인지.  False 면 ``cash`` 는 자리표시자
    # 0 이고, 엔진은 ``equity - invested`` 로 가용현금을 유도한다
    # (실계좌 매수 가능금액 조회 실패 케이스, 2026-11-09).
    cash_known: bool = True

    def held_qty(self, code: str) -> int:
        """Shares currently held for *code*."""
        return _sum_qty(self.positions, code)

    def open_position_count(self) -> int:
        """Number of distinct codes currently held (broker truth)."""
        codes = set()
        for row in self.positions:
            code = _norm(row.get("code"))
            if code:
                codes.add(code)
        return len(codes)

    def invested_amount(self) -> float:
        """Total cost basis of the held positions (``qty x avg_price``)."""
        total = 0.0
        for row in self.positions:
            qty = _float(row.get("qty", row.get("hldg_qty")))
            price = _float(row.get("avg_price", row.get("price")))
            if qty and price:
                total += qty * price
        return total

    def committed_qty(self, code: str) -> int:
        """Shares already committed via outstanding buy orders for *code*."""
        total = 0
        for order in self.open_orders:
            side = str(order.get("side", order.get("bs_code", ""))).lower()
            if side != "buy" and str(order.get("bs_code", "")) != "2":
                continue
            if _norm(order.get("code")) == _norm(code):
                total += _int(order.get("qty", 0))
        return total

    def quote_price(self, code: str) -> Optional[float]:
        """Return the last quote for *code* if known (else None)."""
        value = self.quotes.get(_norm(code))
        return _float(value) if value is not None else None


def market_is_open(
    now: datetime,
    open_time: str = "09:00",
    close_time: str = "15:30",
) -> bool:
    """Return True when *now* (KST) falls in the Mon-Fri 09:00-15:30 session.

    A naive *now* is interpreted as KST.  The session is half-open:
    ``open <= time < close``.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    now = now.astimezone(KST)
    if now.weekday() >= 5:
        return False
    start = datetime.strptime(open_time, "%H:%M").time()
    end = datetime.strptime(close_time, "%H:%M").time()
    current = now.time()
    return start <= current < end


class TraderEngine:
    """Composition root and gate pipeline for trader-core."""

    def __init__(
        self,
        bridge: Any,
        feed_client: Any,
        config: Optional[Config] = None,
        journal: Optional[Journal] = None,
    ) -> None:
        """Wire the engine to a bridge, a feed client, config and journal.

        ``bridge`` must expose the :class:`BridgeClient` surface (or be a
        :class:`MockBridge`); ``feed_client`` exposes ``fetch()``.
        """
        self.bridge = bridge
        self.feed_client = feed_client
        self.config = config if config is not None else Config()
        self.journal = journal

    # ------------------------------------------------------------------ #
    # Public pipeline
    # ------------------------------------------------------------------ #
    def scan(self, now: Optional[datetime] = None) -> DecisionReport:
        """Fetch the feed, pull the live account and evaluate every candidate."""
        try:
            snapshot = self.feed_client.fetch()
        except Exception as exc:  # noqa: BLE001 - surface as a blocked report
            report = DecisionReport(feed_fresh=False, feed_error=str(exc))
            return report
        account = self._pull_account()
        return self.evaluate(snapshot, now=now, account=account)

    def evaluate(
        self,
        snapshot: FeedSnapshot,
        now: Optional[datetime] = None,
        account: Optional[AccountState] = None,
        daily_count: Optional[int] = None,
    ) -> DecisionReport:
        """Evaluate a snapshot against the gate pipeline."""
        now = now if now is not None else datetime.now(KST)
        account = account if account is not None else self._pull_account()
        now = now if now.tzinfo is not None else now.replace(tzinfo=KST)

        feed_fresh = snapshot.is_fresh(now, self.config.feed_max_age)
        kill_active = kill_switch_active(self.config.kill_switch_path)
        market_open = market_is_open(
            now, self.config.market_open, self.config.market_close
        )
        if daily_count is None:
            daily_count = (
                self.journal.trade_count_today(now)
                if self.journal is not None
                else 0
            )

        decisions: List[Decision] = []
        # What this batch has already planned.  The gates read the *broker*
        # snapshot (taken once per cycle), which cannot see plans made later in
        # the same loop, so without these counters one cycle overshoots the
        # portfolio caps by up to (candidates - remaining room): on 2026-09-11
        # 10:43 the engine planned 3 buys while holding 9 against
        # ``max_open_positions = 10`` and ended the cycle holding 12.
        held_codes = set()
        for row in account.positions:
            code = _norm(row.get("code"))
            if code:
                held_codes.add(code)
        batch_new_codes: List[str] = []
        batch_invested = 0.0
        # 경로별로 '이 사이클에서 이미 계획한 종목' — 경로 슬롯 게이트(_slot_allows)가
        # 같은 사이클의 앞선 계획까지 세도록 스크리너별 집합으로 따로 센다.
        batch_planned_by_screener: Dict[str, set] = {}
        for screener in self.config.screener_names:
            for candidate in snapshot.candidates.get(screener, []):
                decision = self._evaluate_candidate(
                    snapshot,
                    candidate,
                    screener,
                    now,
                    feed_fresh,
                    kill_active,
                    market_open,
                    daily_count,
                    account,
                    batch_new_positions=len(batch_new_codes),
                    batch_invested=batch_invested,
                    batch_same_screener=len(
                        batch_planned_by_screener.get(screener, set())
                    ),
                )
                decisions.append(decision)
                plan = decision.plan
                code = _norm(getattr(candidate, "stock_code", ""))
                if plan is not None and plan.side == "buy" and code:
                    batch_planned_by_screener.setdefault(screener, set()).add(code)
                    if code not in held_codes:
                        batch_new_codes.append(code)
                        held_codes.add(code)
                    batch_invested += float(plan.qty) * float(plan.price or 0.0)
                if self.journal is not None:
                    self._journal_decision(decision)

        return DecisionReport(
            generated_at=snapshot.generated_at,
            feed_fresh=feed_fresh,
            market_open=market_open,
            kill_switch=kill_active,
            kill_switch_reason=kill_switch_reason(self.config.kill_switch_path)
            if kill_active
            else "",
            account=account,
            decisions=decisions,
        )

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def execute(
        self, plan: OrderPlan, account: Optional[AccountState] = None
    ) -> Dict[str, Any]:
        """Route a fully gated *plan* to the bridge and journal the fill.

        Market orders first pass the CWE-20 reference-price guard.  In dry-run
        mode with a real bridge the order is suppressed and reported back.
        """
        if self.config.dry_run and isinstance(self.bridge, BridgeClient):
            return {
                "dry_run": True,
                "plan": plan.as_dict(),
                "message": "dry-run: order suppressed (no bridge call)",
            }

        price: Optional[float] = None
        reference: Optional[float] = None
        if plan.order_type == "market":
            ref = self._reference_price(plan.code, account)
            if ref is None or ref <= 0:
                raise OrderBlockedError(
                    "market order for {0} has no recent reference quote "
                    "(CWE-20 guard)".format(plan.code)
                )
            reference = float(ref)
        else:
            if plan.price is None or float(plan.price) <= 0:
                raise OrderBlockedError(
                    "limit order for {0} requires a positive price".format(plan.code)
                )
            price = float(plan.price)

        try:
            filled = self.bridge.order(
                side=plan.side,
                code=plan.code,
                qty=plan.qty,
                price=int(price) if price is not None else None,
                order_type=plan.order_type,
            )
        except BridgeError:
            raise
        # Journal price: the broker's echo when it carries one, else the limit
        # price, else the market reference quote.  A market order echoes price
        # ``0`` (the broker fills at the market), and journaling that 0 booked a
        # whole position as a loss -- observed live 2026-09-10: 026910 was
        # stopped out at ~3,755 but recorded as closed at 0 = -121,500 KRW.
        echo = _float(filled.get("price"))
        fill_price = echo if (echo is not None and echo > 0) else (price or reference or 0.0)
        # The order echo is not a fill confirmation.  Ask the broker what it
        # holds before the row claims a position: an accepted-but-unfilled order
        # journaled as a position occupies a concurrent-position slot and blocks
        # the per-code entry guard for the rest of the day.
        confirm = self._confirm_entry(plan) if plan.side == "buy" else None
        qty = int(plan.qty)
        if confirm is not None:
            if confirm.get("status") == "partial" and _int(confirm.get("filled_qty")) > 0:
                qty = _int(confirm["filled_qty"])
            broker_price = _float(confirm.get("avg_price"))
            if broker_price is not None and broker_price > 0:
                fill_price = broker_price
        self._journal_fill(plan, filled, fill_price, qty=qty)
        result: Dict[str, Any] = dict(filled)
        if confirm is not None:
            result["fill_confirm"] = confirm
        return result

    def _confirm_entry(self, plan: OrderPlan) -> Dict[str, Any]:
        """Ask the broker what actually happened to a just-submitted buy.

        Returns ``{"status": filled|partial|working|unconfirmed, ...}``:

        * ``filled``      - the broker holds it, or a working order reports the
                            full quantity filled;
        * ``partial``     - a working order reports ``0 < filled_qty < qty``;
        * ``working``     - the order is still working with nothing filled;
        * ``unconfirmed`` - neither a position nor a working order is visible
                            (or the broker state could not be read).

        Only ``filled`` may be reported to the owner as a position.  The other
        verdicts reserve the journal row (so a real fill keeps its per-trade
        stop/take-profit levels) but must be re-checked before the slot is
        trusted -- see ``runner/loop.py::_verify_pending_entries``.
        """
        if not bool(getattr(self.config, "confirm_entry_fills", True)):
            return {"status": "unconfirmed", "note": "fill confirmation disabled by config"}
        code = _norm(plan.code)
        try:
            orders = [
                row for row in self.bridge.open_orders()
                if _norm(row.get("code")) == code
            ]
            positions = {
                _norm(row.get("code")): row for row in self.bridge.positions()
            }
        except Exception as exc:  # noqa: BLE001 - confirmation is best effort
            return {
                "status": "unconfirmed",
                "note": "broker state unavailable: {0}".format(str(exc)[:120]),
            }
        position = positions.get(code) or {}
        held_qty = _int(position.get("qty"))
        filled_qty = sum(_int(row.get("filled_qty")) for row in orders)
        working_qty = sum(
            _int(row.get("cancelable_qty"))
            or max(0, _int(row.get("qty")) - _int(row.get("filled_qty")))
            for row in orders
        )
        order_no = next(
            (row.get("order_no") for row in orders if row.get("order_no")), None
        )
        planned = int(plan.qty)
        if filled_qty >= planned or (not orders and held_qty > 0):
            verdict: Dict[str, Any] = {
                "status": "filled",
                "filled_qty": planned,
                "position_qty": held_qty,
                "order_no": order_no,
                "note": (
                    "broker holds {0} share(s)".format(held_qty)
                    if held_qty else "no working order"
                ),
            }
            # The broker's own average is only *this* order's fill price when the
            # position is exactly what it bought; otherwise it is a blend with a
            # holding that was already there.
            avg = _float(position.get("avg_price"))
            if avg is not None and avg > 0 and held_qty == planned:
                verdict["avg_price"] = avg
            return verdict
        if filled_qty > 0:
            return {
                "status": "partial",
                "filled_qty": filled_qty,
                "working_qty": working_qty,
                "position_qty": held_qty,
                "order_no": order_no,
                "note": "partial fill at the broker",
            }
        if orders:
            return {
                "status": "working",
                "filled_qty": 0,
                "working_qty": working_qty,
                "position_qty": held_qty,
                "order_no": order_no,
                "note": "order still working (nothing filled)",
            }
        return {
            "status": "unconfirmed",
            "filled_qty": 0,
            "position_qty": 0,
            "note": "no position and no working order",
        }

    def liquidation_plans(
        self, positions: List[Dict[str, Any]], reason: str = "liquidation"
    ) -> List[OrderPlan]:
        """Turn open positions into market sell plans (kill-switch/close-all).

        Quantity is the sellable quantity when the bridge reports it,
        otherwise the full position quantity.
        """
        plans: List[OrderPlan] = []
        for position in positions:
            code = _norm(position.get("code"))
            qty = _int(position.get("sellable_qty", position.get("qty", 0)))
            if not code or qty <= 0:
                continue
            plans.append(
                OrderPlan(
                    side="sell",
                    code=code,
                    qty=qty,
                    price=None,
                    order_type="market",
                    entry_reason=reason,
                    screener="liquidation",
                    name=str(position.get("name", "") or ""),
                )
            )
        return plans

    # ------------------------------------------------------------------ #
    # Per-candidate gate evaluation
    # ------------------------------------------------------------------ #
    def _evaluate_candidate(
        self,
        snapshot: FeedSnapshot,
        candidate: Candidate,
        screener: str,
        now: datetime,
        feed_fresh: bool,
        kill_active: bool,
        market_open: bool,
        daily_count: int,
        account: AccountState,
        batch_new_positions: int = 0,
        batch_invested: float = 0.0,
        batch_same_screener: int = 0,
    ) -> Decision:
        """Evaluate one candidate; ``batch_*`` carry what earlier plans in this
        same evaluate() call have already committed (see :meth:`evaluate`)."""
        gates: List[GateResult] = []
        price = _float(candidate.close_price)
        score = _float(candidate.score)

        # 1. feed fresh
        if feed_fresh:
            gates.append(GateResult("feed_fresh", True, "feed generated_at within max age"))
        else:
            gates.append(
                GateResult(
                    "feed_fresh",
                    False,
                    "feed is stale (generated_at={0})".format(
                        snapshot.generated_at.isoformat()
                    ),
                )
            )
        # 2. kill switch
        if not kill_active:
            gates.append(GateResult("kill_switch", True, "no kill switch file"))
        else:
            gates.append(
                GateResult(
                    "kill_switch",
                    False,
                    kill_switch_reason(self.config.kill_switch_path),
                )
            )
        # 3. market hours
        needs_open = self.config.market_hours_only or (
            screener == "daytrading"
            and self.config.daytrading_requires_market_open
        )
        if needs_open and not market_open:
            gates.append(
                GateResult(
                    "market_hours",
                    False,
                    "outside KST trading session"
                    + (" (daytrading)" if screener == "daytrading" else ""),
                )
            )
        else:
            gates.append(
                GateResult("market_hours", True, "market hours ok")
            )
        # 4. daily trade count
        if risk.check_daily_trade_count(daily_count, self.config.daily_max_trades):
            gates.append(
                GateResult(
                    "daily_trade_count",
                    True,
                    "today {0} < max {1}".format(daily_count, self.config.daily_max_trades),
                )
            )
        else:
            gates.append(
                GateResult(
                    "daily_trade_count",
                    False,
                    "daily trade count {0} >= max {1}".format(
                        daily_count, self.config.daily_max_trades
                    ),
                )
            )

        # 5. balance / position / order-amount caps (needs a sized qty)
        win_rate, avg_win, avg_loss = self._prior_stats(snapshot, screener)
        plan: Optional[OrderPlan] = None
        if price is None or price <= 0:
            gates.append(
                GateResult("balance_position_amount", False, "no usable price for sizing")
            )
        else:
            equal_weight = (
                str(getattr(self.config, "sizing_mode", "kelly")).lower()
                == "equal_weight"
            )
            if equal_weight:
                # 예수금(또는 equity)을 남은 슬롯 수로 나눠 균등 배분한다.
                # "최대 3종목 · 예수금 100%" 요청(2026-11-09)의 기본 경로.
                qty, invest = self._equal_weight_qty(
                    price=price,
                    account=account,
                    batch_invested=batch_invested,
                    slots_used=account.open_position_count()
                    + max(0, int(batch_new_positions)),
                )
                f_eff = (invest / account.equity) if account.equity > 0 else 0.0
                kelly_qty = qty
            else:
                qty, invest = risk.sized_qty(
                    equity=account.equity,
                    price=price,
                    win_rate=win_rate,
                    avg_win=avg_win,
                    avg_loss=avg_loss,
                    kelly_fraction=self.config.kelly_fraction,
                    kelly_cap=self.config.kelly_cap,
                    max_position_pct=self.config.max_position_pct,
                    max_order_amount=self.config.max_order_amount,
                )
                f_star = risk.kelly_fraction(win_rate, avg_win, avg_loss)
                f_eff = min(
                    f_star * max(0.0, self.config.kelly_fraction),
                    self.config.kelly_cap,
                    self.config.max_position_pct,
                )
                kelly_qty = qty  # before the equal-risk cap (attribution)
            # Volatility-aware stop + equal-risk sizing.  The candidate's own day
            # range decides how far the stop sits, and the size shrinks so the
            # KRW at risk per position stays constant across names.
            day_range_pct = _float(candidate.extras.get("day_range_pct")) or 0.0
            day_high = _float(candidate.extras.get("day_high"))
            off_high_pct = None
            if day_high and day_high > 0:
                # negative = below the day's high (the chase metric)
                off_high_pct = (price - day_high) / day_high * 100.0
            stop_pct = risk.volatility_stop_pct(
                day_range_pct=day_range_pct,
                floor_pct=self.config.stop_loss_pct,
                factor=self.config.stop_range_factor,
                cap_pct=self.config.max_stop_pct,
            )
            tp_pct = self.config.take_profit_pct
            if self.config.stop_loss_pct > 0:
                # keep the intended reward/risk ratio when the stop widens
                tp_pct = stop_pct * (
                    self.config.take_profit_pct / self.config.stop_loss_pct
                )
            risk_qty = 0
            if not equal_weight:
                # Kelly 경로에서만 동일위험 캡을 적용한다 — 균등배분 모드의
                # 목표 금액은 이미 종목당 상한(1/3)과 총예산으로 제한된다.
                risk_qty = risk.risk_capped_qty(
                    equity=account.equity,
                    price=price,
                    stop_pct=stop_pct,
                    risk_per_trade_pct=self.config.risk_per_trade_pct,
                )
                if risk_qty > 0 and risk_qty < qty:
                    qty = risk_qty
                    invest = qty * price

            if qty <= 0:
                gates.append(
                    GateResult(
                        "balance_position_amount",
                        False,
                        "size rounds to zero shares (sizing mode {0}, stop {1:.1%}, "
                        "cash {2:,.0f})".format(
                            self.config.sizing_mode, stop_pct, self._available_cash(account)
                        ),
                    )
                )
            elif not risk.check_buyable(
                self._available_cash(account), invest, reserve=0.0
            ):
                gates.append(
                    GateResult(
                        "balance_position_amount",
                        False,
                        "insufficient cash: need {0:.0f} have {1:.0f}".format(
                            invest, account.cash
                        ),
                    )
                )
            elif not risk.check_position_limit(
                held_qty=account.held_qty(candidate.stock_code),
                existing_qty=account.committed_qty(candidate.stock_code),
                additional=qty,
                max_position_pct=self.config.max_position_pct,
                equity=account.equity,
                price=price,
            ):
                gates.append(
                    GateResult(
                        "balance_position_amount",
                        False,
                        "would breach per-stock {0:.0%} equity cap".format(
                            self.config.max_position_pct
                        ),
                    )
                )
            elif invest > self.config.max_order_amount:
                gates.append(
                    GateResult(
                        "balance_position_amount",
                        False,
                        "order amount {0:.0f} exceeds max_order_amount {1:.0f}".format(
                            invest, self.config.max_order_amount
                        ),
                    )
                )
            elif not self._slot_allows(
                screener, planned_same_screener=batch_same_screener
            ):
                # 경로 슬롯(swing 3 / daytrading 2)은 전역 cap 검사보다 먼저 본다 —
                # slot_rules.can_enter 와 같은 순서(경로 → 전역)로, 어떤 상한이
                # 막았는지가 게이트 문구에 그대로 남는다.
                cap = slot_rules.slot_cap(
                    screener,
                    self.config.max_open_positions,
                    self.config.max_open_positions_by_screener,
                )
                gates.append(
                    GateResult(
                        "balance_position_amount",
                        False,
                        "{0} 슬롯 포화 또는 산정 불가(경로 상한 {1})".format(
                            screener, cap
                        ),
                    )
                )
            elif not risk.check_open_positions(
                count=account.open_position_count() + max(0, int(batch_new_positions)),
                cap=self.config.max_open_positions,
            ):
                gates.append(
                    GateResult(
                        "balance_position_amount",
                        False,
                        "already holding {0} codes (+{1} planned this cycle) "
                        "(max_open_positions {2})".format(
                            account.open_position_count(),
                            max(0, int(batch_new_positions)),
                            self.config.max_open_positions,
                        ),
                    )
                )
            elif not risk.check_invested_ratio(
                invested=account.invested_amount() + max(0.0, float(batch_invested)),
                additional=invest,
                equity=account.equity,
                cap=self.config.max_invested_pct,
            ):
                gates.append(
                    GateResult(
                        "balance_position_amount",
                        False,
                        "invested {0:,.0f} + {1:,.0f} planned + {2:,.0f} would exceed {3:.0%} of "
                        "equity {4:,.0f}".format(
                            account.invested_amount(),
                            max(0.0, float(batch_invested)),
                            invest,
                            self.config.max_invested_pct,
                            account.equity,
                        ),
                    )
                )
            else:
                gates.append(
                    GateResult(
                        "balance_position_amount",
                        True,
                        "qty {0} invest {1:.0f}".format(qty, invest),
                    )
                )
                plan = OrderPlan(
                    side="buy",
                    code=candidate.stock_code,
                    qty=qty,
                    price=price,
                    order_type=self.config.order_type,
                    entry_reason=candidate.reason[:200],
                    screener=screener,
                    name=candidate.stock_name,
                    kelly_f=f_eff,
                    stop_pct=stop_pct,
                    tp_pct=tp_pct,
                    algo=algo.entry_record(
                        screener=screener,
                        scoring=str((snapshot.filters or {}).get("scoring") or ""),
                        params=snapshot.filters,
                        candidate={
                            "rank": candidate.rank,
                            "pct": candidate_change_pct(candidate),
                            "volume": _float(candidate.extras.get("volume")),
                            "turnover": _float(candidate.extras.get("turnover")),
                            "score": score,
                            "day_range_pct": day_range_pct or None,
                            "off_high_pct": off_high_pct,
                            "limit_up": bool(candidate.extras.get("limit_up")),
                            # 모델 메타(계약 v1.1): 실집행 결과를 모델 확률과 대조(캘리브레이션·
                            # 기여도)하려면 저널에 남아야 한다. 2026-09-29 환류 루프 ④.
                            "ml_prob": candidate.ml_prob,
                            "ml_auc": candidate.ml_auc,
                            "ml_horizon_days": candidate.ml_horizon_days,
                        },
                        sizing={
                            "model": "equal-weight"
                            if equal_weight
                            else (
                                "kelly+risk-parity"
                                if self.config.risk_per_trade_pct > 0
                                else "kelly-only"
                            ),
                            "kelly_f": f_eff,
                            "kelly_qty": kelly_qty,
                            "risk_qty": risk_qty,
                            "final_qty": qty,
                            "risk_per_trade_pct": self.config.risk_per_trade_pct,
                            "equity": account.equity,
                        },
                        stop={
                            "model": "vol-range" if day_range_pct > 0 else "fixed",
                            "stop_pct": stop_pct,
                            "tp_pct": tp_pct,
                            "floor_pct": self.config.stop_loss_pct,
                            "range_factor": self.config.stop_range_factor,
                            "trailing_stop_pct": self.config.trailing_stop_pct,
                            "trailing_arm_pct": self.config.trailing_arm_pct,
                        },
                    ),
                )

        # 6. signal sanity
        sane = True
        detail = "signal sane"
        if score is None or score <= self.config.min_score:
            sane = False
            detail = "score {0} not > min_score {1}".format(score, self.config.min_score)
        elif price is None or price <= 0:
            sane = False
            detail = "price {0} is not positive".format(price)
        gates.append(GateResult("signal_sane", sane, detail))
        if not sane:
            plan = None

        # A decision only carries a plan when EVERY gate passed.
        if not all(gate.ok for gate in gates):
            plan = None

        decision = Decision(
            screener=screener,
            code=candidate.stock_code,
            name=candidate.stock_name,
            score=score,
            price=price,
            reason=candidate.reason,
            gates=gates,
            plan=plan,
        )
        return decision

    # ------------------------------------------------------------------ #
    # Priors / account / journal helpers
    # ------------------------------------------------------------------ #
    def _prior_stats(
        self, snapshot: FeedSnapshot, screener: str
    ) -> tuple:
        """Return ``(win_rate, avg_win, avg_loss)`` from feed stats or priors.

        Feed values may be strings; anything missing/invalid falls back to the
        config priors without raising (empty ``screener_stats`` safe).
        The BC-250 scoring summary expresses ``win_rate`` as a percentage
        (e.g. 60.0) — normalise to a fraction; ``avg_win_pct``/``avg_loss_pct``
        are mean returns of winners/losers in %, used only as a ratio (b).
        """
        stats = snapshot.stats_for(screener)
        win_rate = _float(stats.get("win_rate"))
        if win_rate is not None and win_rate > 1.0:
            win_rate = win_rate / 100.0  # percent -> fraction
        avg_win = _float(stats.get("avg_win_pct"))
        if avg_win is None:
            avg_win = _float(stats.get("avg_win"))
        avg_loss = _float(stats.get("avg_loss_pct"))
        if avg_loss is None:
            avg_loss = _float(stats.get("avg_loss"))
        cfg = self.config
        if win_rate is None or not (0.0 < win_rate < 1.0):
            win_rate = cfg.default_win_rate
        if avg_win is None or avg_win <= 0:
            avg_win = cfg.default_avg_win
        if avg_loss is None or avg_loss <= 0:
            avg_loss = cfg.default_avg_loss
        return win_rate, avg_win, avg_loss

    def _slot_allows(self, screener: str, planned_same_screener: int = 0) -> bool:
        """경로별 동시보유 슬롯 게이트 — journal.open_trades(screener=) 로 센다.

        WHY 저널인가: 브로커 포지션에는 스크리너 귀속이 없어 "스윙 3 / 단타 2"를
        셀 수 없다. 미청산 저널 행이 유일한 귀속 출처다.
        실측 2026-10-07 15:03 스냅샷(위 조회, trade_journal.sqlite3): 미청산 3행
        전부 swing 이고 daytrading 은 사상 0건 — 전역 cap 3 이 swing 3 으로
        포화돼 단타가 한 번도 진입하지 못했다. 전역 cap 을 5 로 올린 뒤에도
        경로별 상한(swing 3 / daytrading 2)은 이 게이트가 지킨다.

        fail-closed: 저널이 없거나 조회가 실패하면 False(진입 차단) — 슬롯을 셀 수
        없는데 열어주면 경로 상한을 보증할 수 없다. close 처럼 슬롯이 정의되지 않은
        경로(GLOBAL_FALLBACK_SCREENERS)는 항상 허용하고 전역 cap 게이트가 대신 막는다.
        """
        if screener in slot_rules.GLOBAL_FALLBACK_SCREENERS:
            return True
        if self.journal is None:
            return False
        try:
            rows = self.journal.open_trades(screener=screener)
            # 종목 단위로 센다 — 같은 종목의 중복 미청산 행은 1 로
            # ('동시 보유 종목 수' 의미, 전역 게이트와 같은 단위).
            used = len({_norm(row.get("code")) for row in rows
                        if _norm(row.get("code"))})
            cap = slot_rules.slot_cap(
                screener,
                self.config.max_open_positions,
                self.config.max_open_positions_by_screener,
            )
        except Exception:  # noqa: BLE001 - fail-closed
            return False
        used += max(0, int(planned_same_screener))
        return used < cap

    def _pull_account(self) -> AccountState:
        """Read the account snapshot from the bridge.

        ``cash`` is only trustworthy when the bridge actually carried it.  The
        live Creon bridge can fail its buyable query (``buyable_error``
        12275/12340) and then omits ``cash`` entirely; that case is marked
        ``cash_known=False`` so the sizing path can derive cash from
        ``equity - invested`` instead of silently reading a placeholder 0.
        """
        balance = self.bridge.balance()
        total_eval = _float(balance.get("total_eval_amount")) or 0.0
        raw_cash = _float(balance.get("cash"))
        if raw_cash is None:
            raw_cash = _float(balance.get("withdrawable"))
        cash_known = raw_cash is not None
        cash = float(raw_cash) if cash_known else 0.0
        equity = _float(balance.get("equity"))
        if equity is None or equity <= 0:
            equity = cash + total_eval
        positions = self.bridge.positions()
        open_orders = self.bridge.open_orders()
        return AccountState(
            cash=float(cash),
            cash_known=cash_known,
            equity=float(equity or 0.0),
            positions=list(positions) if isinstance(positions, list) else [],
            open_orders=list(open_orders) if isinstance(open_orders, list) else [],
        )

    def _available_cash(self, account: AccountState) -> float:
        """주문에 쓸 수 있는 현금 — 브리지가 ``cash`` 를 못 줄 때의 유도값 포함.

        실계좌(783247576)에서 ``CpTdNew5331A``(매수 가능금액) 조회가
        ``12275 종목코드를 확인하십시요`` / ``12340 주문가능금액 확인바랍니다`` 로
        실패하면 ``/balance`` 에 ``cash`` 키가 아예 없다.  그 상태에서
        ``account.cash`` 를 그대로 쓰면 0 이 되어 **모든 매수가 조용히 차단**된다
        (2026-10-09 이후 신규 진입 0건과 같은 증상).

        그래서 cash 가 0 이면 ``equity - invested`` 로 유도한다.  equity 는
        (cash + 평가금액) 이므로 이 값은 (현금 + 미실현손익) 과 같고, 평가손실
        상태에서는 현금을 **과소**평가하는 보수적 값이다.  계좌가 정말 비었으면
        0 이 그대로 남는다.
        """
        cash = float(account.cash or 0.0)
        if getattr(account, "cash_known", True):
            return cash
        equity = float(account.equity or 0.0)
        return max(0.0, equity - account.invested_amount())

    def _equal_weight_qty(
        self,
        price: float,
        account: AccountState,
        batch_invested: float = 0.0,
        slots_used: int = 0,
    ) -> tuple:
        """예수금을 남은 슬롯 수로 나눈 균등배분 수량(정수 주).

        목표 금액 = min(남은 예산 / 남은 슬롯, equity x 종목당 상한,
                        가용현금, 1회 주문 상한)
        * 남은 예산 = equity x max_invested_pct - (보유 원가 + 이번 배치 계획)
        * 남은 슬롯 = max(1, max_open_positions - 보유 종목수 - 이번 배치 신규)
        """
        try:
            eq = float(account.equity or 0.0)
            px = float(price)
        except (TypeError, ValueError):
            return 0, 0.0
        if eq <= 0 or px <= 0:
            return 0, 0.0
        cap = max(1, int(self.config.max_open_positions or 1))
        slots_left = max(1, cap - max(0, int(slots_used)))
        budget = eq * float(self.config.max_invested_pct)
        remaining = budget - account.invested_amount() - max(0.0, float(batch_invested))
        order_cap = float(self.config.max_order_amount or 0.0) or float("inf")
        target = min(
            max(0.0, remaining) / slots_left,
            eq * float(self.config.max_position_pct),
            self._available_cash(account),
            order_cap,
        )
        if target <= 0:
            return 0, 0.0
        qty = int(target // px)
        return qty, qty * px

    def _reference_price(
        self, code: str, account: Optional[AccountState] = None
    ) -> Optional[float]:
        """Resolve a recent reference price for a market order."""
        code = _norm(code)
        if account is not None:
            known = account.quote_price(code)
            if known is not None:
                return known
        try:
            quotes = self.bridge.quotes([code])
        except BridgeError:
            return None
        quote = quotes.get(code, {})
        current = quote.get("current") if isinstance(quote, dict) else None
        if current is None and isinstance(quote, dict):
            current = quote.get("price")
        return _float(current)

    def _journal_decision(self, decision: Decision) -> None:
        if self.journal is None:
            return
        gates_json = json.dumps(
            [g.as_dict() for g in decision.gates], ensure_ascii=False
        )
        plan_json = (
            json.dumps(decision.plan.as_dict(), ensure_ascii=False)
            if decision.plan is not None
            else None
        )
        self.journal.record_decision(
            screener=decision.screener,
            code=decision.code,
            name=decision.name,
            score=decision.score,
            gates_json=gates_json,
            plan_json=plan_json,
            reason=decision.blocked_reason() or "ok",
        )

    def _journal_fill(
        self,
        plan: OrderPlan,
        filled: Dict[str, Any],
        fill_price: float,
        qty: Optional[int] = None,
    ) -> None:
        """Write the fill into the journal.

        ``qty`` overrides ``plan.qty`` when the broker's own fill is known to be
        smaller (a partial entry fill): the journal must record what the broker
        holds, never the size that was asked for.
        """
        if self.journal is None:
            return
        if plan.side == "buy":
            self.journal.record_trade(
                side="buy",
                code=plan.code,
                name=plan.name,
                qty=int(qty) if qty else int(plan.qty),
                price=fill_price,
                order_type=plan.order_type,
                order_resp_json=json.dumps(filled, ensure_ascii=False, default=str),
                entry_reason=plan.entry_reason,
                screener=plan.screener,
                algo_json=algo.dumps(plan.algo),
                stop_pct=plan.stop_pct,
                tp_pct=plan.tp_pct,
                kelly_f=plan.kelly_f,
            )
        else:
            open_row = self.journal.open_trade_for(plan.code)
            if open_row is not None:
                # Keep the entry attribution and add how it was closed, so a
                # review can ask "which rule took us out?" per trade.
                patch = algo.exit_record(
                    rule=plan.algo.get("rule") or plan.entry_reason or "sell",
                    trigger_price=fill_price,
                    price_source=str(plan.algo.get("price_source") or "order_fill"),
                    qty=plan.qty,
                    peak=plan.algo.get("peak"),
                )
                self.journal.close_trade(
                    trade_id=open_row["id"],
                    exit_price=fill_price,
                    exit_reason=plan.entry_reason or "sell",
                    algo_patch=algo.merge(open_row.get("algo_json"), patch),
                )
            else:
                self.journal.record_trade(
                    side="sell",
                    code=plan.code,
                    name=plan.name,
                    qty=plan.qty,
                    price=fill_price,
                    order_type=plan.order_type,
                    order_resp_json=json.dumps(filled, ensure_ascii=False, default=str),
                    entry_reason=plan.entry_reason,
                    screener=plan.screener,
                    algo_json=algo.dumps(plan.algo),
                )


# ---------------------------------------------------------------------- #
# Small numeric helpers
# ---------------------------------------------------------------------- #
def _float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
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


def _norm(code: Any) -> Optional[str]:
    if code is None:
        return None
    return str(code).strip().upper().lstrip("A") if code else None


def _sum_qty(rows: List[Dict[str, Any]], code: str) -> int:
    plain = _norm(code)
    total = 0
    for row in rows:
        if _norm(row.get("code")) == plain:
            total += _int(row.get("qty", 0))
    return total
