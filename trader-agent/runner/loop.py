"""MarketLoop: the market-hours operating state machine.

Drives the trader-core engine/monitor against the creon bridge during the KST
session.  One :meth:`MarketLoop.run_once` executes a full cycle
``CHECK -> DECIDE -> ACT -> MONITOR`` (the whole pipeline when inside the
schedule window) and returns a :class:`LoopReport`.  :meth:`run_forever`
repeats cycles on ``interval_seconds`` until the day closes or the loop halts.

The loop owns the R1-R4 rules (see :mod:`runner.rules`) on top of the engine's
own gate pipeline, persists its HALT flag + daily counters to the JSON state
file (so remote ops can steer it), always journals through trader-core, and
notifies through :class:`runner.notify.Notifier`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from trader_core import (
    Journal,
    OrderBlockedError,
    OrderPlan,
    PositionMonitor,
    TraderEngine,
    kill_switch_active,
    kill_switch_reason,
    market_is_open,
)
from trader_core.config import KST
from trader_core.algo import exit_record, summary as algo_summary
from trader_core.clockcheck import ClockGuard
from trader_core.feed import normalize_code
from trader_core.net import urlopen as local_urlopen
from trader_core.profiles import profile_for
from trader_core.reconcile import reconcile
from trader_core.tickwatch import tick_exit_candidates

from .config import RunnerConfig
from .notify import Notifier
from .schedule import cycle_interval
from .rules import (
    apply_r1,
    apply_r2,
    gate_blocked_codes,
    heat_blocks,
    r3_deviation_ok,
    r3_reason,
    r4_triggered,
    stale_candidates,
)
from .state import StateStore, default_state

HALT_REASON_LOSS = "daily_loss_limit"
# Pre-filter quote batches: one big /quotes call exceeds the bridge 5s timeout
# (measured 2026-09-29 14:52: 20 codes -> timeout -> every candidate dropped).
PRE_FILTER_QUOTE_CHUNK = 5
# Prefix of the halt reason written when the health checks fail repeatedly.
# MarketLoop._is_check_halt keys the auto-resume behaviour off it, so _check
# must keep building its reason with this prefix.
CHECK_HALT_PREFIX = "check failures"


def _now_kst() -> datetime:
    return datetime.now(KST)


def _as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=KST)
    return value.astimezone(KST)


def _to_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _equity_of(balance: Any) -> Optional[float]:
    """Best-effort equity from a bridge /balance payload.

    Prefers an explicit ``equity`` field; falls back to ``cash`` +
    ``total_eval_amount`` when no equity is reported (the read-only bridge
    exposes no cash figure for a fully-cash simulation account).  Returns None
    only when nothing numeric is present.
    """
    if not isinstance(balance, dict):
        return None
    explicit = _to_float(balance.get("equity"))
    if explicit is not None:
        return explicit
    cash = _to_float(balance.get("cash"))
    total = _to_float(balance.get("total_eval_amount"))
    if cash is None and total is None:
        return None
    return (cash or 0.0) + (total or 0.0)


def _parse_hhmm(value: str) -> int:
    hour, minute = str(value).split(":", 1)
    return int(hour) * 60 + int(minute)


def _iso_date(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _plain_code(value: Any) -> Optional[str]:
    """Return the plain 6-digit code for a bridge/screener code, else None."""
    return normalize_code(value)


def _age_seconds(stamp: Any, now: datetime) -> Optional[float]:
    """Return the age in seconds of an ISO timestamp, or None when unusable."""
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    try:
        return (_as_kst(now) - _as_kst(parsed)).total_seconds()
    except (TypeError, ValueError, OverflowError):  # pragma: no cover - defensive
        return None


@dataclass
class LoopReport:
    """The outcome of a single loop cycle."""

    ts: datetime
    phase: str = "idle"  # idle|check|decide|act|monitor|close|halted
    market_open: bool = False
    fresh: bool = False
    checked: bool = False
    check_error: Optional[str] = None
    consecutive_failures: int = 0
    decisions: int = 0
    blocked_screeners: int = 0
    r5_blocked: int = 0
    plans: int = 0
    acted: int = 0
    exits: int = 0
    exit_reasons: List[str] = field(default_factory=list)
    dry_run: bool = True
    halted: bool = False
    halt_reason: str = ""
    # True when the halt is a connectivity *pause* that the loop clears by
    # itself once the link is back (see MarketLoop._auto_resume_halt).  A
    # non-resumable halt (R4 loss limit, kill-switch file) ends the process.
    resumable: bool = False
    closed: bool = False
    reasons: List[str] = field(default_factory=list)
    # Post-close journal<->broker reconciliation summary (empty unless the
    # close cycle ran with ``close_reconcile`` enabled).
    reconcile: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "phase": self.phase,
            "market_open": self.market_open,
            "fresh": self.fresh,
            "checked": self.checked,
            "check_error": self.check_error,
            "consecutive_failures": self.consecutive_failures,
            "decisions": self.decisions,
            "blocked_screeners": self.blocked_screeners,
            "r5_blocked": self.r5_blocked,
            "plans": self.plans,
            "acted": self.acted,
            "exits": self.exits,
            "exit_reasons": list(self.exit_reasons),
            "dry_run": self.dry_run,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "resumable": self.resumable,
            "closed": self.closed,
            "reasons": list(self.reasons),
            "reconcile": dict(self.reconcile),
        }


class MarketLoop:
    """Market-hours operating loop (deterministic rules, no LLM)."""

    def __init__(
        self,
        bridge: Any,
        feed_client: Any,
        config: RunnerConfig,
        journal: Optional[Journal] = None,
        state: Optional[StateStore] = None,
        notifier: Optional[Notifier] = None,
        engine: Optional[TraderEngine] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Wire the loop to a bridge, feed client, config and optional extras.

        ``bridge`` exposes the :class:`trader_core.BridgeClient` surface (or a
        ``MockBridge``); ``feed_client`` exposes ``fetch()``.  The journal is
        always created from ``config.journal_path`` when not supplied (the loop
        always journals).  ``state``/``notifier``/``engine``/``log`` are
        injectable for tests.
        """
        self.bridge = bridge
        self.feed_client = feed_client
        self.config = config
        self.journal = journal if journal is not None else Journal(config.journal_path)
        self.state_store = state if state is not None else StateStore(config.state_path)
        self.state: Dict[str, Any] = self.state_store.read()
        self.engine_cfg = config.engine_config()
        self.engine = engine if engine is not None else TraderEngine(
            bridge, feed_client, self.engine_cfg, self.journal
        )
        self.monitor = PositionMonitor(
            bridge, self.engine_cfg, self.journal,
            profiles=getattr(self.config, "screener_profiles", None),
        )
        self.notifier = notifier if notifier is not None else Notifier(
            config.webhook_url, log=log
        )
        self._log = log if log is not None else _default_log
        self._sleep = time.sleep
        # Reason lines from the cycle last written to the log.  The state file is
        # overwritten every cycle, so without this the day's own explanation
        # ("heat blocked close", "no enterable candidates") left no durable trace
        # in runner.log -- measured 2026-10-02: the close list was heat-blocked
        # from the open (avg +17.0% > +15.0% ceiling) and runner.log read
        # "monitor: no open positions" for the whole session.  Keyed by
        # (date, reasons) so a change is logged once and a stable set is silent.
        self._logged_reason_key: Optional[tuple] = None
        # Codes whose realtime subscription this process already asked for.
        self._tick_subscribed: set = set()
        # Wall-clock sanity: a skewed clock once made this loop trade and then
        # run its day-end logic at the wrong moment.  Entries are blocked while
        # the local clock disagrees with an external HTTP Date.
        self.clock_guard = ClockGuard.from_config(self.engine_cfg)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def run_once(self, now: Optional[datetime] = None) -> LoopReport:
        """Execute one full cycle and return its :class:`LoopReport`."""
        now = _as_kst(now if now is not None else _now_kst())
        report = LoopReport(ts=now, dry_run=bool(self.config.dry_run))
        report.market_open = market_is_open(
            now, self.engine_cfg.market_open, self.engine_cfg.market_close
        )
        # Re-read the state file every cycle so a remote HALT/resume or CLI
        # halt/resume (a different process) is honoured immediately.
        self.state = self.state_store.read()
        self._roll_day(now)

        mode, why = self._schedule_mode(now)
        if mode == "closed":
            self._close_day(now, report)
            self._finish_report(report)
            return report

        halted_reason = self._halted()
        if halted_reason is not None and self._auto_resume_halt(halted_reason, now, report):
            # A check-failure halt is a *pause*, not a hard stop: the link may
            # come back on its own (Creon re-login, bridge restart) and nothing
            # else has to happen.  Keep the process alive and retry every cycle.
            halted_reason = None
        if halted_reason is not None:
            report.halted = True
            report.halt_reason = halted_reason
            report.phase = "halted"
            report.resumable = self._is_check_halt(halted_reason)
            report.reasons.append("cycle skipped: halted ({0})".format(halted_reason))
            self._finish_report(report)
            return report

        if mode == "idle":
            report.phase = "idle"
            report.reasons.append("outside schedule window ({0})".format(why))
            self._finish_report(report)
            return report

        # Active window: run the full pipeline.
        report.phase = "check"
        snapshot = self._check(now, report)
        if snapshot is None or not report.checked:
            self._finish_report(report)  # CHECK failed / halted this cycle
            return report
        self._decide_and_act(now, snapshot, report)
        self._verify_pending_entries(now, report)
        self._monitor(now, report)
        self._finish_report(report)
        return report

    def run_forever(self, now: Optional[datetime] = None) -> LoopReport:
        """Run cycles until the day closes or the loop halts.

        An injected ``now`` (KST) is used for the first cycle only; subsequent
        cycles use the real clock.  Returns the final :class:`LoopReport`.
        """
        report: Optional[LoopReport] = None
        if self.clock_guard is not None:
            try:
                self._log("clock check: " + self.clock_guard.describe())
            except Exception as exc:  # noqa: BLE001
                self._log("clock check unavailable: {0}".format(exc))
        while True:
            if now is not None:
                report = self.run_once(now)
                now = None
            else:
                report = self.run_once()
            assert report is not None
            if report.closed or (report.halted and not report.resumable):
                return report
            self._log_cycle_reasons(report)
            delay = float(self.config.interval_seconds)
            focus = None
            if report.phase == "idle":
                wait = self._seconds_until_schedule(report.ts)
                if wait is not None and wait > 0:
                    delay = min(delay, max(5.0, wait))
            else:
                # Focus windows (open / early afternoon / closing auction) run a
                # shorter cadence so stops and close-screener entries are not
                # missed between 5-minute cycles.
                delay, focus = cycle_interval(
                    report.ts, delay, getattr(self.config, "focus_windows", None)
                )
            if focus is not None:
                self._log("focus window {0}: cycle every {1:.0f}s".format(
                    focus.get("window"), delay
                ))
            self._log("cycle done ({0}); sleeping {1:.0f}s".format(
                report.phase, delay
            ))
            self._tick_watch_wait(delay, report)

    def _log_cycle_reasons(self, report: LoopReport) -> None:
        """Log the cycle's reason lines whenever the reason set changes.

        ``report.reasons`` is where a cycle that bought nothing says *why*
        (R1/heat block, per-code staleness skips, window/drift drops, gate
        vetoes).  Before this, those lines only reached runner.log when the
        process exited, so a live session's log showed a book idling with no
        explanation and the state file (overwritten every cycle) was the only
        place the reason ever lived.  Logging on change keeps a stable reason
        set from spamming; the date is part of the key, so a new day re-logs
        the same reasons instead of swallowing them.
        """
        key = (report.ts.date(), tuple(report.reasons))
        if key == self._logged_reason_key:
            return
        self._logged_reason_key = key
        for reason in report.reasons:
            self._log("  - {0}".format(reason))

    # ------------------------------------------------------------------ #
    # Sub-cycle exit watch (realtime cache)
    # ------------------------------------------------------------------ #
    def _tick_watch_wait(self, delay: float, report: LoopReport) -> None:
        """Sleep *delay* seconds while watching the realtime cache for breaches.

        The base cadence (30 s in the focus windows, 300 s otherwise) exists
        because every *cycle* costs broker requests.  Polling the bridge's
        cached StockCur ticks costs none, so the exit lines can be re-checked
        every ``tick_watch_seconds`` -- a position that crosses its stop or
        gives back its trailing stop is then closed within about a second
        instead of waiting for the next cycle.  Returns as soon as an exit has
        been sent so the next cycle re-reads state and journals it.
        """
        seconds = float(getattr(self.config, "tick_watch_seconds", 0.0) or 0.0)
        if seconds <= 0 or float(delay) <= 0 or not self._tick_watch_armed(report):
            self._sleep(delay)
            return
        self._tick_subscribe()
        deadline = time.monotonic() + float(delay)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._sleep(min(seconds, remaining))
            try:
                if self._tick_watch_pass(report):
                    return  # an exit went out: start the next cycle now
            except Exception as exc:  # noqa: BLE001 - never wedge the loop
                self._log("tick watch failed: {0}".format(exc))
                # Keep the cadence: a broken watch must not turn into fast
                # cycles (each cycle costs broker requests).
                self._sleep(max(0.0, deadline - time.monotonic()))
                return

    def _tick_watch_armed(self, report: Optional[LoopReport]) -> bool:
        """True when the watch may run: session open, day open, not halted."""
        if report is None or report.closed or report.halted:
            return False
        return bool(report.market_open)

    def _tick_subscribe(self) -> None:
        """Ask the bridge to publish realtime ticks for the held codes.

        Only codes not already requested are sent (the hub keeps a set, but a
        repeated call would re-run the subscription work on its pump thread);
        a failure is logged and never blocks trading.
        """
        codes = self._tick_codes()
        pending = [c for c in codes if c not in self._tick_subscribed]
        if not pending:
            return
        try:
            self.bridge.subscribe(pending)
        except Exception as exc:  # noqa: BLE001
            self._log("tick watch: subscribe failed: {0}".format(exc))
            return
        self._tick_subscribed.update(pending)

    def _tick_codes(self) -> List[str]:
        """Plain codes of the open journal rows (the positions we manage)."""
        try:
            rows = self.journal.open_trades()
        except Exception:  # noqa: BLE001 - journal trouble must not stop the loop
            return []
        codes: List[str] = []
        for row in rows:
            code = _plain_code(row.get("code"))
            if code and code not in codes:
                codes.append(code)
        return codes

    def _tick_watch_pass(self, report: LoopReport) -> bool:
        """One breach check; returns True when an exit order was sent.

        Detection uses the *cache only* (no broker request).  Only when a
        candidate appears does this call ``/positions`` once and re-run the same
        test against the broker's own mark: the cached tick is a trigger, the
        broker mark is the authority (the 2026-09-10 divergence left a -17.8 %
        position un-stopped), and the broker's sellable quantity is what gets
        sold.
        """
        watch = float(getattr(self.config, "tick_watch_seconds", 0.0) or 0.0)
        if watch <= 0:
            return False
        max_age = float(getattr(self.config, "tick_watch_max_age_seconds", 5.0) or 0.0)
        max_age = max_age if max_age > 0 else 5.0
        cache = self.bridge.cached_quotes()
        if not cache:
            return False
        rows = self.journal.open_trades()
        if not rows:
            return False
        now = _now_kst()
        row_positions = [
            {
                "code": row.get("code"),
                "qty": row.get("qty"),
                "sellable_qty": row.get("qty"),
            }
            for row in rows
        ]
        candidates = self._tick_candidates(row_positions, rows, cache, now, max_age)
        if not candidates:
            return False

        try:
            broker_positions = self.bridge.positions()
        except Exception as exc:  # noqa: BLE001
            self._log("tick watch: breach seen but /positions failed: {0}".format(exc))
            return False
        marks = {}
        by_code = {}
        for position in broker_positions:
            code = _plain_code(position.get("code"))
            qty = int(_to_float(position.get("qty", 0)) or 0)
            ev = _to_float(position.get("eval_amount"))
            if not code:
                continue
            by_code[code] = position
            if qty > 0 and ev and ev > 0:
                marks[code] = {"current": ev / float(qty), "ts": now.timestamp()}
        confirmed = {
            breach.code: breach
            for breach in self._tick_candidates(row_positions, rows, marks, now, max_age)
        }

        acted = False
        for candidate in candidates:
            position = by_code.get(candidate.code)
            if position is None:
                self._log("tick watch: {0} breach ignored (no broker position)".format(
                    candidate.code
                ))
                continue
            if candidate.code not in confirmed:
                self._log("tick watch: {0} breach not confirmed by the broker mark "
                          "({1}) -> holding".format(candidate.code, candidate.reason))
                continue
            qty = int(_to_float(position.get("sellable_qty", position.get("qty", 0))) or 0)
            if qty <= 0:
                continue
            plan = OrderPlan(
                side="sell",
                code=candidate.code,
                name=str(position.get("name", "") or ""),
                qty=qty,
                price=None,
                order_type="market",
                entry_reason="tick {0}".format(candidate.reason),
                algo=exit_record(
                    rule="tick_{0}".format(candidate.reason),
                    trigger_price=min(candidate.price, marks[candidate.code]["current"]),
                    price_source="realtime_cache_confirmed_by_broker_mark",
                    qty=qty,
                ),
            )
            try:
                result = self.engine.execute(plan)
            except Exception as exc:  # noqa: BLE001 - keep trading other names
                report.reasons.append(
                    "tick exit failed {0}: {1}".format(candidate.code, exc)
                )
                continue
            report.exits += 1
            report.exit_reasons.append("tick " + candidate.reason)
            report.reasons.append(
                "tick exit {0} ({1})".format(candidate.code, candidate.reason)
            )
            self._log("tick exit {0}: {1}".format(
                candidate.code,
                result.get("status") if isinstance(result, dict) else "ok",
            ))
            acted = True
        return acted

    def _tick_candidates(self, positions, rows, ticks, now, max_age):
        """Run :func:`tick_exit_candidates` with this loop's level defaults."""
        return tick_exit_candidates(
            positions,
            rows,
            ticks,
            now=now,
            max_age_seconds=max_age,
            default_stop_pct=float(self.engine_cfg.stop_loss_pct or 0.0),
            default_tp_pct=float(self.engine_cfg.take_profit_pct or 0.0),
            arm_pct=float(getattr(self.engine_cfg, "trailing_arm_pct", 0.0) or 0.0),
            trail_pct=float(getattr(self.engine_cfg, "trailing_stop_pct", 0.0) or 0.0),
        )

    def halt(self, reason: str) -> None:
        """Set the soft HALT flag in the state file (no liquidation)."""
        self.state["loop"]["halt"] = True
        self.state["loop"]["halt_reason"] = reason
        self._persist_state()
        self._log("HALT: {0}".format(reason))
        self.notifier.send("HALT: {0}".format(reason))

    def resume(self) -> None:
        """Clear the soft HALT flag (kill-switch file is untouched)."""
        self.state["loop"]["halt"] = False
        self.state["loop"]["halt_reason"] = ""
        self._persist_state()
        self._log("RESUME")
        self.notifier.send("RESUME")

    def _halted(self) -> Optional[str]:
        """Return the halt reason when the loop must not trade, else None.

        A HALT flag in the state file OR the presence of the kill-switch file
        (hard stop used by remote ops / R4) stops the cycle.
        """
        loop_state = self.state.get("loop", {})
        if loop_state.get("halt"):
            return str(loop_state.get("halt_reason") or "halt flag set")
        if kill_switch_active(self.engine_cfg.kill_switch_path):
            return kill_switch_reason(self.engine_cfg.kill_switch_path)
        return None

    @staticmethod
    def _is_check_halt(reason: str) -> bool:
        """True when *reason* is the connectivity halt written by _check."""
        return str(reason or "").startswith(CHECK_HALT_PREFIX)

    def _probe_ok(self, now: datetime) -> bool:
        """Re-run the check steps without touching counters or state.

        Used while paused on a connectivity halt: the pause may only clear when
        the *same* things the check verifies work again.  The link AND the feed
        both matter -- a stale feed is a check failure too, so a healthy
        ``/health`` alone is not evidence that the previous failure is over.
        """
        try:
            health = self.bridge.health()
            if not bool((health or {}).get("connected")):
                return False
            self.bridge.balance()
            snapshot = self.feed_client.fetch()
            return bool(snapshot.is_fresh(now, self.engine_cfg.feed_max_age))
        except Exception:  # noqa: BLE001 - still broken: stay paused
            return False

    def _auto_resume_halt(
        self, reason: str, now: datetime, report: LoopReport
    ) -> bool:
        """Clear a connectivity halt once the checks pass again.

        Returns True when the halt was cleared (the cycle then runs normally).
        Only the check-failure halt is auto-cleared: the link comes back on its
        own after a Creon re-login or a bridge restart, and killing the process
        over it turned a 17-minute outage into a stuck loop that needed someone
        to notice and type ``resume`` (2026-09-21).  An R4 loss halt and the
        kill-switch file stay hard stops that need a human.
        """
        if not self._is_check_halt(reason):
            return False
        if not bool(getattr(self.config, "auto_resume_check_halt", True)):
            return False
        if not self._probe_ok(now):
            return False
        self.state["loop"]["halt"] = False
        self.state["loop"]["halt_reason"] = ""
        self.state["check"]["consecutive_failures"] = 0
        self.state["check"]["last_error"] = None
        self._persist_state()
        self._log("RESUME (auto): checks pass again after '{0}'".format(reason))
        self.notifier.send("RESUME (auto): checks pass again")
        report.reasons.append("auto-resume: {0}".format(reason))
        return True

    def status(self) -> Dict[str, Any]:
        """Return a snapshot of the loop for operators/status CLI."""
        return {
            "now_kst": _now_kst().isoformat(),
            "state": self.state_store.read(),
            "engine_config": self.engine_cfg.as_dict(),
            "dry_run": bool(self.config.dry_run),
            "market_open": self.engine_cfg.market_open,
            "market_close": self.engine_cfg.market_close,
        }

    # ------------------------------------------------------------------ #
    # Day / schedule bookkeeping
    # ------------------------------------------------------------------ #
    def _schedule_mode(self, now: datetime) -> tuple:
        """Return ``(mode, why)``: active|idle|closed relative to the window.

        The active window is ``market_open - start_before_open_min`` through
        ``market_close + end_after_close_min`` on weekdays; the engine's own
        market-hours gate still governs tradable minutes inside that window.
        """
        if now.weekday() >= 5:
            return "closed", "weekend"
        minute = now.hour * 60 + now.minute
        start = _parse_hhmm(self.config.market_open) - int(
            self.config.start_before_open_min
        )
        end = _parse_hhmm(self.config.market_close) + int(
            self.config.end_after_close_min
        )
        if minute < start:
            return "idle", "before schedule window"
        if minute < end:
            return "active", ""
        return "closed", "after schedule window"

    def _seconds_until_schedule(self, now: datetime) -> Optional[float]:
        """Seconds until the next schedule-window start (None if inside/after)."""
        if now.weekday() >= 5:
            return None
        minute = now.hour * 60 + now.minute
        start = _parse_hhmm(self.config.market_open) - int(
            self.config.start_before_open_min
        )
        if minute >= start:
            return None
        target = now.replace(
            hour=start // 60, minute=start % 60, second=0, microsecond=0
        )
        return (target - now).total_seconds()

    def _roll_day(self, now: datetime) -> None:
        """Reset daily counters when the KST date changes (persist old summary)."""
        today = _iso_date(now)
        daily = self.state.get("daily", {})
        if daily.get("date") == today:
            return
        old_summary = daily.get("summary") if isinstance(daily, dict) else None
        if old_summary is not None:
            self.state["last_summary"] = old_summary
        fresh = default_state()["daily"]
        fresh["date"] = today
        self.state["daily"] = fresh
        self.state["check"]["consecutive_failures"] = 0
        # A pending entry belongs to the day it was placed: yesterday's codes
        # must never be matched against today's journal rows.
        self.state["pending_entries"] = {}
        self._log("day rollover -> {0}".format(today))
        self._persist_state()

    def _persist_state(self) -> None:
        self.state_store.write(self.state)

    def _finish_report(self, report: LoopReport) -> None:
        self.state["last_cycle"] = report.as_dict()
        self._persist_state()

    # ------------------------------------------------------------------ #
    # CHECK
    # ------------------------------------------------------------------ #
    def _check(self, now: datetime, report: LoopReport) -> Optional[Any]:
        """Verify bridge + feed + freshness; returns the feed snapshot or None.

        On failure the cycle is skipped with a recorded reason and the
        consecutive-failure counter advances; reaching the configured maximum
        auto-HALTs (state file only - no liquidation).
        """
        failure: Optional[str] = None

        try:
            health = self.bridge.health()
            if not bool((health or {}).get("connected")):
                failure = "bridge health: not connected"
        except Exception as exc:  # noqa: BLE001 - typed below by message
            failure = "bridge health: {0}".format(exc)

        equity: Optional[float] = None
        if failure is None:
            try:
                balance = self.bridge.balance()
                equity = _equity_of(balance)
                if equity is None:
                    failure = "bridge balance: no usable cash/equity"
            except Exception as exc:  # noqa: BLE001
                failure = "bridge balance: {0}".format(exc)

        snapshot = None
        if failure is None:
            try:
                snapshot = self.feed_client.fetch()
            except Exception as exc:  # noqa: BLE001 - fail safe on feed trouble
                failure = "feed fetch: {0}".format(exc)
            else:
                if not snapshot.is_fresh(now, self.engine_cfg.feed_max_age):
                    failure = "feed stale (generated_at={0})".format(
                        snapshot.generated_at.isoformat()
                    )

        if failure is not None:
            report.checked = False
            report.check_error = failure
            report.reasons.append("CHECK skip: {0}".format(failure))
            consecutive = int(self.state["check"].get("consecutive_failures", 0)) + 1
            self.state["check"]["consecutive_failures"] = consecutive
            self.state["check"]["last_error"] = failure
            report.consecutive_failures = consecutive
            self._log("CHECK failure {0}: {1}".format(consecutive, failure))
            self._persist_state()
            if consecutive >= int(self.config.max_consecutive_check_failures):
                reason = "{0} {1}: {2}".format(
                    CHECK_HALT_PREFIX, consecutive, failure
                )
                self.halt(reason)
                report.halted = True
                report.halt_reason = reason
                report.phase = "halted"
                # A connectivity halt is a pause: the process keeps running and
                # clears it once the bridge answers again.
                report.resumable = True
            return None

        # Success: reset the counter and record the day's start equity once.
        report.checked = True
        report.fresh = True
        report.consecutive_failures = 0
        self.state["check"]["consecutive_failures"] = 0
        self.state["check"]["last_error"] = None
        daily = self.state["daily"]
        if daily.get("start_equity") is None and equity is not None:
            daily["start_equity"] = equity
            self._log("CHECK: day start equity {0:.0f}".format(equity))
        self._persist_state()
        return snapshot

    # ------------------------------------------------------------------ #
    # DECIDE + ACT
    # ------------------------------------------------------------------ #
    def _tradable_snapshot(
        self,
        snapshot: Any,
        stale_codes: Dict[str, str],
        blocked_screeners: Dict[str, str],
        now: datetime,
    ) -> tuple:
        """Return ``(snapshot, notes)`` keeping only candidates enterable *now*.

        The engine allocates the concurrent-position budget
        (``max_open_positions``) over every candidate it is shown, but the loop
        drops candidates *after* that (stale signal, closed entry window, price
        past R3).  A dropped candidate therefore wastes a slot and the
        next-best name never gets a plan — measured 2026-09-29: the three
        leading close candidates all failed R3 (계획가 대비 -7.2%/-13.1%/+13.6%)
        while #4 (+1.3%, inside the band) was blocked as
        "already +3 planned this cycle".  Filter first, then let the engine
        spend the slots.

        R1/heat are deliberately computed on the *unfiltered* list (they judge
        the candidate list as a regime proxy), so this runs after them.
        """
        notes: List[str] = []
        candidates = getattr(snapshot, "candidates", {}) or {}
        if not candidates:
            return snapshot, notes
        codes: List[str] = []
        for screener, items in candidates.items():
            if screener in blocked_screeners:
                continue
            for candidate in items or []:
                code = getattr(candidate, "stock_code", "")
                if code and code not in stale_codes:
                    codes.append(code)
        quotes: Dict[str, Any] = {}
        unique = sorted(set(codes))
        for start in range(0, len(unique), PRE_FILTER_QUOTE_CHUNK):
            chunk = unique[start:start + PRE_FILTER_QUOTE_CHUNK]
            try:
                quotes.update(self.bridge.quotes(chunk) or {})
            except Exception as exc:  # noqa: BLE001 - never wedge the cycle
                # 시세를 못 받으면 **거르지 않는다**(원본 스냅샷 유지). 여기서 실패를 닫아버리면
                # 전 후보가 탈락해 매매가 통째로 막힌다 — 실제로 2026-09-29 14:52 에 20종목
                # 일괄 조회가 브리지 5초 타임아웃에 걸려 `price 20`(전량 탈락)이 났다.
                # 계획 단계의 기존 R3 검사가 종목별로 다시 확인하므로 안전하다.
                self._log(
                    "warning: pre-filter quote fetch failed ({0}) - "
                    "skipping the price pre-filter this cycle".format(exc)
                )
                return snapshot, notes
        filtered: Dict[str, List[Any]] = {}
        for screener, items in candidates.items():
            kept: List[Any] = []
            dropped_stale = dropped_window = dropped_price = 0
            profile = profile_for(screener, self.config.screener_profiles)
            window_ok, _why = profile.entry_allowed(
                now, self.engine_cfg.market_open, self.engine_cfg.market_close
            )
            for candidate in items or []:
                code = getattr(candidate, "stock_code", "")
                if screener in blocked_screeners or code in stale_codes:
                    dropped_stale += 1
                    continue
                if not window_ok:
                    dropped_window += 1
                    continue
                entry = quotes.get(code) or {}
                price = entry.get("current") if isinstance(entry, dict) else None
                if price is None:
                    price = entry.get("price") if isinstance(entry, dict) else None
                if not r3_deviation_ok(
                    getattr(candidate, "close_price", None), price,
                    self.config.r3_max_deviation_pct,
                ):
                    dropped_price += 1
                    continue
                kept.append(candidate)
            filtered[screener] = kept
            if dropped_stale or dropped_window or dropped_price:
                notes.append(
                    "PRE filter {0}: {1} -> {2} (stale/blocked {3}, window {4}, price {5})".format(
                        screener, len(items or []), len(kept),
                        dropped_stale, dropped_window, dropped_price,
                    )
                )
        try:
            filtered_snapshot = replace(snapshot, candidates=filtered)
        except Exception:  # noqa: BLE001 - unknown snapshot type: keep the original
            return snapshot, notes
        return filtered_snapshot, notes

    def _decide_and_act(
        self, now: datetime, snapshot: Any, report: LoopReport
    ) -> None:
        """Run the engine gate pipeline, then apply R1/R2/R3 and execute."""
        report.phase = "decide"
        # R1/heat judge the candidate *list* (regime proxy) -> compute them on the
        # unfiltered snapshot, then hand the engine only what it can actually buy.
        blocked_screeners = apply_r1(snapshot, self.config)
        for screener, reason in blocked_screeners.items():
            report.reasons.append("R1 block {0}: {1}".format(screener, reason))

        # Heat gauge: a list can be strong *and* late.  Above the ceilings the
        # candidates are the top of the move, so block them like R1 does.
        for screener, reason in heat_blocks(snapshot, self.config).items():
            if screener not in blocked_screeners:
                blocked_screeners[screener] = reason
                report.reasons.append("HEAT block {0}: {1}".format(screener, reason))
        # Count after the heat gauge: taking it before left a heat-only block
        # invisible in the state file (measured 2026-09-30: close blocked by
        # heat while blocked_screeners still read 1 for the R1-blocked swing).
        report.blocked_screeners = len(blocked_screeners)

        # Candidate-level staleness: gate 1 only seals the snapshot, so a fresh
        # file can still carry days-old per-stock signals (2026-09-29: a close
        # candidate detected 09-23 was tradeable on 09-29).  Drop just those
        # codes -- the rest of the list stays usable.
        stale_codes = stale_candidates(snapshot, self.config, now)
        for code in sorted(stale_codes):
            report.reasons.append("STALE skip {0}: {1}".format(code, stale_codes[code]))

        # Position slots must go to enterable candidates only (see
        # _tradable_snapshot): filter, then evaluate.
        tradable, pre_notes = self._tradable_snapshot(
            snapshot, stale_codes, blocked_screeners, now
        )
        for note in pre_notes:
            report.reasons.append(note)
        try:
            eval_report = self.engine.evaluate(tradable, now=now)
        except Exception as exc:  # noqa: BLE001 - never crash the loop
            report.reasons.append("DECIDE aborted: {0}".format(exc))
            return
        report.decisions = len(eval_report.decisions)

        entry_count = self._entries_today()
        r2_blocked, r2_reason = apply_r2(
            entry_count, self.config.r2_max_entries_per_day
        )

        # Wall-clock gate: with a skewed clock the whole session model (market
        # hours, focus windows, day rollover) is wrong, so no new entries.
        clock_blocked = False
        if self.clock_guard is not None:
            try:
                if self.clock_guard.blocks():
                    clock_blocked = True
                    report.reasons.append("CLOCK " + self.clock_guard.describe())
            except Exception as exc:  # noqa: BLE001 - never wedge the cycle
                self._log("warning: clock check failed ({0})".format(exc))

        plans: List[OrderPlan] = []
        window_blocked: Dict[str, str] = {}
        # Anti-churn: with the daily cap at 100 and a 60s cadence in the focus
        # windows, the same candidate list would be re-bought every cycle.  One
        # entry per code per day keeps "100 entries" meaning 100 *different*
        # names rather than the same six names sixteen times.
        entered_today: set = set()
        if getattr(self.config, "r2_one_entry_per_code_per_day", True) and self.journal:
            try:
                entered_today = set(self.journal.codes_entered_today(now))
            except Exception as exc:  # noqa: BLE001 - guard must never break trading
                self._log("warning: journal lookup failed ({0})".format(exc))
        dup_skipped: List[str] = []
        rejected: List[str] = []
        for decision in eval_report.decisions:
            plan = decision.plan
            if plan is None or plan.side != "buy":
                # Engine gate rejected the candidate (per-stock cap, open-position
                # cap, cash, ...).  Surface the first failing gate detail so the
                # cycle log explains why a candidate list produced no orders
                # (2026-09-29: 6 candidates -> 3 orders with no reason recorded).
                detail = ""
                for gate in getattr(decision, "gates", ()) or ():
                    if not getattr(gate, "ok", True):
                        detail = str(
                            getattr(gate, "detail", "") or getattr(gate, "gate", "")
                        )
                        break
                if detail:
                    rejected.append(
                        "{0} {1}: {2}".format(
                            decision.screener,
                            getattr(plan, "code", "") or "-",
                            detail,
                        )
                    )
                continue
            if decision.screener in blocked_screeners:
                continue
            if plan.code in stale_codes:
                continue
            if plan.code in entered_today:
                dup_skipped.append(plan.code)
                continue
            # Per-screener entry window: a 종가 list may only be bought into the
            # closing auction, a swing list any time, etc. (profiles.py).
            profile = profile_for(decision.screener, self.config.screener_profiles)
            allowed, why = profile.entry_allowed(
                now, self.engine_cfg.market_open, self.engine_cfg.market_close
            )
            if not allowed:
                window_blocked.setdefault(decision.screener, why)
                continue
            # Attribution: which profile (entry window + exit mode) governs it.
            plan.algo = dict(plan.algo or {})
            plan.algo["exit_mode"] = getattr(profile, "exit_mode", "") or ""
            plan.algo["entry_window"] = getattr(profile, "entry_window", "") or ""
            plan.algo["screener"] = plan.algo.get("screener") or decision.screener
            quote = self._quote_price(plan.code)
            if not r3_deviation_ok(
                plan.price, quote, self.config.r3_max_deviation_pct
            ):
                report.reasons.append(
                    "R3 skip {0}: {1}".format(
                        plan.code,
                        r3_reason(plan.price, quote, self.config.r3_max_deviation_pct),
                    )
                )
                continue
            plans.append(plan)
        for screener, why in sorted(window_blocked.items()):
            report.reasons.append("P skip {0}: {1}".format(screener, why))
        if dup_skipped:
            report.reasons.append(
                "D skip: already entered today ({0})".format(
                    ", ".join(sorted(dup_skipped))
                )
            )
        if rejected:
            shown = "; ".join(rejected[:5])
            if len(rejected) > 5:
                shown += " (+{0} more)".format(len(rejected) - 5)
            report.reasons.append("G skip: " + shown)
        report.plans = len(plans)

        if r2_blocked:
            report.reasons.append(r2_reason)
            plans = []  # no new entries; monitor still runs
        elif clock_blocked:
            plans = []  # clock unverified/skewed: monitor + exits only
        elif plans:
            # R2 is a DAILY cap, so it must also bound a single cycle: checking
            # it once before the batch let a 6-candidate cycle open 6 entries
            # against a max-3 budget (observed live 2026-09-10).  Truncate to
            # the remaining budget instead.
            remaining = max(
                0, int(self.config.r2_max_entries_per_day) - entry_count
            )
            if len(plans) > remaining:
                report.reasons.append(
                    "r2: capped this cycle to {0} new entr{1} "
                    "(daily max {2}, already {3})".format(
                        remaining,
                        "y" if remaining == 1 else "ies",
                        self.config.r2_max_entries_per_day,
                        entry_count,
                    )
                )
                plans = plans[:remaining]
            report.plans = len(plans)

        # R5: BC-250 news/sentiment veto on the surviving plans.
        if plans:
            plans, gate_notes = self._apply_news_gate(plans)
            report.r5_blocked = len(gate_notes)
            report.reasons.extend(gate_notes)
            report.plans = len(plans)

        if plans:
            self._act(plans, report, now)
        if report.acted == 0:
            report.phase = "decide"

    def _apply_news_gate(
        self, plans: List[OrderPlan]
    ) -> tuple:
        """Return ``(kept_plans, notes)`` after the R5 news-gate veto.

        Queries the BC-250 news gate once for every surviving code.  An empty
        ``news_gate_url`` disables the gate; when enabled and unreachable the
        loop fails closed (all plans dropped) unless configured otherwise.
        """
        url = (self.config.news_gate_url or "").strip()
        if not url or not plans:
            return plans, []
        codes = sorted({p.code for p in plans})
        timeout = float(getattr(self.config.engine, "request_timeout", 5.0))
        target = "{0}{1}codes={2}&days={3}".format(
            url,
            "&" if "?" in url else "?",
            ",".join(codes),
            int(self.config.news_gate_window_days),
        )
        try:
            with local_urlopen(target, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 - fail safe on gate outage
            reason = "r5: news gate unavailable ({0})".format(str(exc)[:140])
            if self.config.news_gate_fail_closed:
                return [], [reason + " — new entries blocked (fail-closed)"]
            return plans, [reason + " — gate bypassed this cycle"]
        blocked = gate_blocked_codes(payload)
        if not blocked:
            return plans, []
        kept = [p for p in plans if p.code not in blocked]
        notes = [
            "R5 block {0}: {1}".format(code, blocked[code])
            for code in sorted(blocked)
            if any(p.code == code for p in plans)
        ]
        return kept, notes

    def _quote_price(self, code: str) -> Optional[float]:
        """Return the live current price for *code* via the bridge (or None)."""
        try:
            quotes = self.bridge.quotes([code])
        except Exception as exc:  # noqa: BLE001 - fail safe on missing quote
            self._log("quote fetch failed for {0}: {1}".format(code, exc))
            return None
        entry = (quotes or {}).get(code) or {}
        if not isinstance(entry, dict):
            return None
        current = entry.get("current")
        if current is None:
            current = entry.get("price")
        return _to_float(current)

    def _act(
        self, plans: List[OrderPlan], report: LoopReport, now: datetime
    ) -> None:
        """Route each surviving plan through the engine (live or dry-run)."""
        report.phase = "act"
        pending_entries: List[str] = []
        # R2b (2026-11-11 duplicate-order defect): the candidate feed can carry
        # the same code twice in one payload, and every plan of a cycle is built
        # from the SAME AccountState snapshot, so the engine's committed_qty gate
        # cannot see an order placed a millisecond earlier in this batch.  Two
        # live buys of 240810 went out in one cycle that way.  Two independent
        # guards, because either alone has a hole:
        #   * one plan per code per cycle (the feed duplicate),
        #   * a broker preflight: never stack onto a code that already has a
        #     working order (the previous cycle's unfilled order).
        # A failed preflight is fail-CLOSED: a missed entry is cheap, a duplicate
        # is not, and the observed trigger was a bridge timeout, not a quiet link.
        working: Optional[set] = set()
        try:
            working = {
                normalize_code(row.get("code"))
                for row in (self.bridge.open_orders() or [])
                if row.get("code")
            }
        except Exception as exc:  # noqa: BLE001 - BridgeError & friends
            working = None
            self._log("act preflight unavailable (/orders failed): {0}".format(exc))
        seen_codes: set = set()
        for plan in plans:
            code = normalize_code(plan.code) or str(plan.code)
            if code in seen_codes:
                report.reasons.append(
                    "SKIP {0}: duplicate plan in this cycle (feed listed it twice)".format(code)
                )
                self._log("SKIP {0}: duplicate plan in cycle".format(code))
                continue
            seen_codes.add(code)
            if working is None:
                report.reasons.append(
                    "SKIP {0}: /orders preflight failed - refusing to place an order "
                    "that might double up".format(code)
                )
                continue
            if code in working:
                report.reasons.append(
                    "SKIP {0}: working order already live at the broker".format(code)
                )
                self._log("SKIP {0}: working order already live".format(code))
                continue
            try:
                result = self.engine.execute(plan)
            except OrderBlockedError as exc:
                report.reasons.append("order blocked {0}: {1}".format(plan.code, exc))
                continue
            except Exception as exc:  # noqa: BLE001 - BridgeError & friends
                report.reasons.append("order failed {0}: {1}".format(plan.code, exc))
                continue
            if isinstance(result, dict) and result.get("dry_run"):
                report.acted += 1
                report.reasons.append(
                    "dry-run {0} {1} qty {2}".format(plan.side, plan.code, plan.qty)
                )
                if plan.side == "buy":
                    self._bump_entries()
            else:
                # Fill detection: MockBridge returns {"status": "filled", ...};
                # the live bridge client returns the inner order object
                # {"order_no", side, code, qty, price, order_type, message}
                # (no status key).  Anything else is unexpected.
                filled = isinstance(result, dict) and (
                    str(result.get("status", "")).lower() == "filled"
                    or result.get("order_no") is not None
                )
                if filled:
                    report.acted += 1
                    confirm = result.get("fill_confirm") if isinstance(result, dict) else None
                    status = str((confirm or {}).get("status") or "")
                    unconfirmed = plan.side == "buy" and status in ("working", "unconfirmed")
                    if unconfirmed:
                        # The order was *accepted*; the broker has not filled it.
                        # Say exactly that: the echo is not a fill confirmation.
                        report.reasons.append(
                            "{0} order {1} qty {2} @ {3} placed, FILL NOT CONFIRMED "
                            "({4}: {5})".format(
                                plan.side, plan.code, plan.qty, result.get("price"),
                                status, (confirm or {}).get("note"),
                            )
                        )
                        self._mark_pending_entry(plan, result, confirm, now)
                        pending_entries.append(str(plan.code))
                    else:
                        partial_note = ""
                        if status == "partial":
                            partial_note = " [partial: broker filled {0}]".format(
                                (confirm or {}).get("filled_qty")
                            )
                        report.reasons.append(
                            "filled {0} {1} qty {2} @ {3} (order {4}){5}".format(
                                plan.side,
                                plan.code,
                                plan.qty,
                                result.get("price"),
                                result.get("order_no"),
                                partial_note,
                            )
                        )
                    # Algorithm attribution, one line per order (not per cycle,
                    # so the cadence stays untouched): which screener, scoring
                    # mode, sizing path and stop model produced this fill.
                    self._log(
                        "ALGO {0} {1} qty {2} :: {3}".format(
                            plan.side, plan.code, plan.qty,
                            algo_summary(plan.algo),
                        )
                    )
                    if plan.side == "buy":
                        self._bump_entries()
                else:
                    report.reasons.append(
                        "unexpected order result for {0}: {1!r}".format(
                            plan.code, result
                        )
                    )
        if report.acted:
            kind = "dry-run" if self.config.dry_run else "live"
            self.notifier.send("ACT: {0} {1} order(s) placed".format(report.acted, kind))
        if pending_entries:
            self.notifier.send(
                "ACT: {0} entry order(s) unconfirmed (still working at the "
                "broker): {1}".format(len(pending_entries), ", ".join(pending_entries))
            )

    def _mark_pending_entry(
        self,
        plan: OrderPlan,
        result: Dict[str, Any],
        confirm: Optional[Dict[str, Any]],
        now: datetime,
    ) -> None:
        """Remember an entry whose fill the broker has not confirmed yet.

        The journal row exists (so a real fill keeps its own stop/take-profit
        levels) but it must not be trusted as a position: it reserves a
        concurrent-position slot, and a row that never fills has to release
        that slot in-cycle instead of waiting for the day-close reconcile.
        """
        pending = self.state.setdefault("pending_entries", {})
        pending[str(plan.code)] = {
            "order_no": (confirm or {}).get("order_no"),
            "qty": int(plan.qty),
            "price": _to_float(result.get("price")) or _to_float(plan.price) or 0.0,
            "status": str((confirm or {}).get("status") or "unconfirmed"),
            "ts": _as_kst(now).isoformat(),
        }
        self._persist_state()

    def _verify_pending_entries(self, now: datetime, report: LoopReport) -> None:
        """Re-check entry orders whose fill the broker had not confirmed.

        Every cycle, for each pending code:

        * the broker holds it -> confirmed, stop tracking it (the day-close
          reconcile still owns quantity/price corrections);
        * a working order with nothing filled exists -> still coming, keep the
          row and the slot;
        * neither, and the entry is older than ``pending_entry_grace_s`` -> the
          entry never became a position: close the row at its own entry price
          (zero PnL) and free the slot now, instead of blocking it until the
          close.

        The grace period exists because broker positions settle seconds to ~1
        minute after a fill (measured: 3 shares at 14:55:51 -> 37 at 14:57:22):
        an order that just filled can be gone from the working list while the
        position is not visible yet, and closing that row at cost would throw
        away a real holding's own stop/take-profit levels.
        """
        pending = self.state.get("pending_entries") or {}
        if not pending:
            return
        try:
            positions = {
                _plain_code(row.get("code")) for row in self.bridge.positions()
            }
            orders = self.bridge.open_orders()
        except Exception as exc:  # noqa: BLE001 - never kill the cycle
            report.reasons.append("pending entry check failed: {0}".format(exc))
            return
        grace = float(getattr(self.config, "pending_entry_grace_s", 120.0) or 0.0)
        changed = False
        for code in list(pending):
            info = pending.get(code) or {}
            if code in positions:
                self._log("entry {0}: fill confirmed at the broker".format(code))
                del pending[code]
                changed = True
                continue
            still_working = [
                row for row in orders
                if _plain_code(row.get("code")) == code
                and int(row.get("filled_qty") or 0) <= 0
            ]
            if still_working:
                continue
            age = _age_seconds(info.get("ts"), now)
            if age is not None and age < grace:
                report.reasons.append(
                    "entry {0}: no position and no working order yet "
                    "({1:.0f}s old, grace {2:.0f}s)".format(code, age, grace)
                )
                continue
            row = self.journal.open_trade_for(code) if self.journal is not None else None
            if row is not None:
                entry = _to_float(info.get("price")) or _to_float(row.get("price")) or 0.0
                if entry > 0:
                    self.journal.close_trade(
                        trade_id=row["id"],
                        exit_price=float(entry),
                        exit_reason="entry order never filled (verified at the broker)",
                    )
                    report.reasons.append(
                        "entry {0} never filled -> closed at cost, slot freed".format(code)
                    )
                    self._log(
                        "entry {0}: no position and no working order -> closed at "
                        "cost ({1:,.0f}), slot freed".format(code, entry)
                    )
                else:
                    report.reasons.append(
                        "entry {0}: no position, no working order and no price to "
                        "close it at".format(code)
                    )
            del pending[code]
            changed = True
        if changed:
            self.state["pending_entries"] = pending
            self._persist_state()

    def _bump_entries(self) -> None:
        """Count one successful buy fill toward today's R2 entry cap.

        Dry-run fills are tracked separately: simulating a cycle must never
        consume the real daily entry budget, otherwise running ``--dry-run``
        once in the morning would block live trading for the rest of the day.
        """
        key = "dry_run_entries" if self.config.dry_run else "entries"
        self.state["daily"][key] = int(self.state["daily"].get(key, 0)) + 1
        self._persist_state()

    def _entries_today(self) -> int:
        """Return today's entry count for the mode the loop is running in."""
        key = "dry_run_entries" if self.config.dry_run else "entries"
        return int(self.state["daily"].get(key, 0))

    # ------------------------------------------------------------------ #
    # MONITOR + R4
    # ------------------------------------------------------------------ #
    def _monitor(self, now: datetime, report: LoopReport) -> None:
        """Scan positions for stop/tp exits, then apply the R4 loss limit."""
        report.phase = "monitor"
        try:
            monitor_report = self.monitor.heartbeat(now=now)
        except Exception as exc:  # noqa: BLE001 - monitor must not kill loop
            report.reasons.append("monitor heartbeat failed: {0}".format(exc))
            monitor_report = None

        if monitor_report is not None:
            for note in monitor_report.notes:
                self._log("monitor: {0}".format(note))
            for signal in monitor_report.exits:
                plan = OrderPlan(
                    side="sell",
                    code=signal.code,
                    name=signal.name or "",
                    qty=signal.qty,
                    price=None,
                    order_type=signal.order_type,
                    entry_reason="monitor {0}".format(signal.reason),
                    algo=exit_record(
                        rule=signal.reason,
                        trigger_price=signal.price,
                        price_source="quote_or_broker_mark",
                        qty=signal.qty,
                    ),
                )
                try:
                    result = self.engine.execute(plan)
                except Exception as exc:  # noqa: BLE001 - keep going on failures
                    report.reasons.append("exit failed {0}: {1}".format(signal.code, exc))
                    continue
                report.exits += 1
                report.exit_reasons.append(signal.reason)
                report.reasons.append(
                    "exit {0} ({1})".format(signal.code, signal.reason)
                )
                if isinstance(result, dict):
                    detail = result.get("status") or result.get("message") or "ok"
                    self._log("exit {0}: {1}".format(signal.code, detail))
        self._r4(now, report)

    def _r4(self, now: datetime, report: LoopReport) -> None:
        """Daily-loss limit: liquidate everything + halt when breached."""
        try:
            equity_now = _equity_of(self.bridge.balance())
        except Exception as exc:  # noqa: BLE001
            report.reasons.append("R4 balance fetch failed: {0}".format(exc))
            return
        start_equity = self.state["daily"].get("start_equity")
        if r4_triggered(start_equity, equity_now, self.config.r4_loss_limit_pct):
            report.reasons.append(
                "R4: equity {0:.0f} < start {1:.0f} x (1-{2:.0%})".format(
                    equity_now, float(start_equity), self.config.r4_loss_limit_pct
                )
            )
            self._liquidate_all(HALT_REASON_LOSS, report)
            self.halt(HALT_REASON_LOSS)
            self._write_kill_switch(HALT_REASON_LOSS)
            report.halted = True
            report.halt_reason = HALT_REASON_LOSS
            report.phase = "halted"

    def _liquidate_all(self, reason: str, report: LoopReport) -> None:
        """Sell every open position at market (engine gates still apply)."""
        try:
            positions = self.bridge.positions()
        except Exception as exc:  # noqa: BLE001
            report.reasons.append("liquidation: positions fetch failed: {0}".format(exc))
            return
        plans = self.engine.liquidation_plans(list(positions), reason=reason)
        if not plans:
            report.reasons.append("liquidation: no open positions")
            return
        sold = 0
        for plan in plans:
            try:
                result = self.engine.execute(plan)
            except Exception as exc:  # noqa: BLE001
                report.reasons.append(
                    "liquidation sell {0} failed: {1}".format(plan.code, exc)
                )
                continue
            sold += 1
            detail = result.get("message") if isinstance(result, dict) else ""
            self._log("liquidation sold {0}: {1}".format(plan.code, detail))
        report.reasons.append("liquidation: sold {0}/{1} position(s)".format(sold, len(plans)))

    def _write_kill_switch(self, reason_line: str) -> None:
        """Write the kill-switch file (first line carries the reason).

        The engine refuses new orders while this file exists, so a daily-loss
        halt stays hard until an operator removes it.
        """
        path = self.engine_cfg.kill_switch_path
        if not path:
            return
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(reason_line))
            handle.write("\n")

    # ------------------------------------------------------------------ #
    # CLOSE
    # ------------------------------------------------------------------ #
    def _close_day(self, now: datetime, report: LoopReport) -> None:
        """Cancel outstanding orders and write the daily summary into state."""
        report.phase = "close"
        daily = self.state["daily"]
        if daily.get("closed") is True and daily.get("summary") is not None:
            report.closed = True
            report.reasons.append("day already closed")
            return

        cancelled = 0
        try:
            orders = self.bridge.open_orders()
        except Exception as exc:  # noqa: BLE001
            report.reasons.append("CLOSE open_orders failed: {0}".format(exc))
            orders = []
        outstanding = [order for order in orders if isinstance(order, dict)]
        for order in outstanding:
            code = order.get("code")
            order_no = order.get("order_no") or order.get("original_order_no")
            if not code or order_no is None:
                continue
            try:
                qty = order.get("cancelable_qty") or order.get("qty")
                self.bridge.cancel(str(order_no), code, qty=int(qty) if qty else None)
                cancelled += 1
            except Exception as exc:  # noqa: BLE001
                report.reasons.append("CLOSE cancel {0} failed: {1}".format(order_no, exc))
        report.reasons.append("CLOSE cancelled {0} outstanding order(s)".format(cancelled))

        # A working order that never filled is a *phantom* position as soon as
        # the journal has already booked it (the order echo is not a fill
        # confirmation).  Reconcile against the pre-cancel snapshot so the
        # phantom does not eat an entry slot / the per-code guard tomorrow.
        reconcile_summary = self._post_close_reconcile(outstanding, report)

        start_equity = daily.get("start_equity")
        end_equity: Optional[float] = None
        try:
            end_equity = _equity_of(self.bridge.balance())
        except Exception as exc:  # noqa: BLE001
            report.reasons.append("CLOSE balance failed: {0}".format(exc))
        pnl: Optional[float] = None
        if start_equity is not None and end_equity is not None:
            pnl = float(end_equity) - float(start_equity)
        summary = {
            "date": daily.get("date"),
            "start_equity": start_equity,
            "end_equity": end_equity,
            "pnl": pnl,
            "entries": daily.get("entries"),
            "reconcile": reconcile_summary,
        }
        daily["closed"] = True
        daily["closed_pnl"] = pnl
        daily["summary"] = summary
        daily["reconcile"] = reconcile_summary
        report.closed = True
        report.reasons.append(
            "CLOSE summary pnl={0} entries={1}".format(
                pnl if pnl is not None else "n/a", daily.get("entries", 0)
            )
        )
        report.reconcile = reconcile_summary
        self._persist_state()
        self.notifier.send(
            "market close: pnl {0} entries {1}{2}".format(
                pnl if pnl is not None else 0, daily.get("entries", 0),
                _reconcile_note(reconcile_summary),
            )
        )

    # ------------------------------------------------------------------ #
    # Post-close reconciliation
    # ------------------------------------------------------------------ #
    def _post_close_reconcile(
        self, outstanding: List[Dict[str, Any]], report: LoopReport
    ) -> Dict[str, Any]:
        """Compare the journal with the broker once the session is over.

        ``outstanding`` is the pre-cancel ``/orders`` snapshot: an order that
        was still working at the close is the broker's own evidence that
        nothing filled, which is what separates a phantom entry from a real
        position that was sold outside the system.

        Returns a compact summary (also stored under ``daily.reconcile``); a
        bridge failure is reported and never raises, so the day still closes.
        """
        if not bool(getattr(self.config, "close_reconcile", True)):
            return {}
        try:
            positions = self.bridge.positions()
        except Exception as exc:  # noqa: BLE001
            report.reasons.append("CLOSE reconcile positions failed: {0}".format(exc))
            return {}
        positions = [p for p in positions if isinstance(p, dict)]
        codes = [str(p.get("code")) for p in positions if p.get("code")]
        # Journal rows the broker is flat on have no position to quote, but the
        # current price is still the operator's best hint for --fix-exit; the
        # bridge answers both sets in one request.
        try:
            open_codes = [row["code"] for row in self.journal.open_trades()]
        except Exception as exc:  # noqa: BLE001
            report.reasons.append("CLOSE reconcile journal read failed: {0}".format(exc))
            open_codes = []
        for code in open_codes:
            if code and code not in codes:
                codes.append(str(code))
        quotes: Dict[str, Any] = {}
        if codes:
            try:
                quotes = self.bridge.quotes(codes)
            except Exception as exc:  # noqa: BLE001
                report.reasons.append("CLOSE reconcile quotes failed: {0}".format(exc))
        apply = bool(getattr(self.config, "close_reconcile_apply", True))
        try:
            result = reconcile(
                self.journal, positions, quotes=quotes, open_orders=outstanding,
                apply=apply,
                # Never guess a flat close price from the quote: the real exit
                # is a broker fill.  Report those for the operator instead.
                apply_flat_close=False,
            )
        except Exception as exc:  # noqa: BLE001 - the close must still finish
            report.reasons.append("CLOSE reconcile failed: {0}".format(exc))
            return {}

        def _emit(line: str) -> None:
            self._log(line)
            report.reasons.append(line)

        for row in result["unfilled"]:
            _emit(
                "CLOSE unfilled order: {0} {1}주 @ {2:,.0f} (order {3}) - "
                "never became a position, closed at cost (zero PnL)".format(
                    row["code"], row["qty"], row["entry"], row["order_no"],
                )
            )
        for key, label in (
            ("qty_fixed", "partial fill fixed"),
            ("adopted", "adopted from broker"),
            ("reopened", "phantom close reopened"),
            ("corrected", "exit price corrected"),
        ):
            for row in result[key]:
                _emit("CLOSE reconcile {0}: {1}".format(label, _json(row)))
        for row in result["closed"]:
            _emit(
                "CLOSE flat but journal open: {0} {1}주 (trade {2}) - the quote "
                "{3:,.0f} is NOT a fill price, so nothing was written; fix with "
                "tools/reconcile_journal.py --apply --fix-exit {0}=PRICE".format(
                    row["code"], row["qty"], row["trade_id"], row["exit"]
                )
            )
        for row in result["needs_price"]:
            _emit(
                "CLOSE needs price: {0} (trade {1}) - journal open, broker flat, "
                "no unfilled order; fix with tools/reconcile_journal.py".format(
                    row["code"], row["trade_id"]
                )
            )
        for row in result["bogus_exits"]:
            _emit(
                "CLOSE zero-price exit: {0} (trade {1}) - correct with "
                "tools/reconcile_journal.py --fix-exit {0}=PRICE".format(
                    row["code"], row["trade_id"]
                )
            )

        summary: Dict[str, Any] = {"applied": bool(result["applied"])}
        for key in ("unfilled", "qty_fixed", "adopted", "reopened", "corrected",
                    "closed", "needs_price", "bogus_exits"):
            summary[key] = [row["code"] for row in result[key]]
        summary["broker_codes"] = result["broker_codes"]
        summary["journal_open_codes"] = result["journal_open_codes"]
        summary["issues"] = sum(
            len(summary[key]) for key in
            ("unfilled", "qty_fixed", "adopted", "reopened", "corrected",
             "closed", "needs_price", "bogus_exits")
        )
        # ``closed`` is report-only here (see apply_flat_close above) while the
        # others were written; keep that distinction explicit in the state.
        summary["report_only"] = summary["closed"] + summary["needs_price"] + summary["bogus_exits"]
        return summary


def _reconcile_note(summary: Dict[str, Any]) -> str:
    """Return the close-notification suffix for a reconcile summary.

    Empty when there was nothing to report, so a clean day keeps the old
    one-line close message.
    """
    if not summary or not summary.get("issues"):
        return ""
    parts: List[str] = []
    for key, label in (
        ("unfilled", "unfilled"), ("qty_fixed", "qty fixed"),
        ("adopted", "adopted"), ("reopened", "reopened"),
        ("corrected", "exit fixed"), ("report_only", "needs a human"),
    ):
        codes = summary.get(key) or []
        if codes:
            parts.append("{0} {1}".format(label, ",".join(str(c) for c in codes)))
    return " | reconcile: " + "; ".join(parts)


def _json(value: Any) -> str:
    """Serialise a reconcile row for the log (ASCII-safe punctuation)."""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(value)


def _default_log(line: str) -> None:  # pragma: no cover - trivial
    print(line, flush=True)
