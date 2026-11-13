"""Loop-level tests for the sub-cycle exit watch.

``run_forever`` used to sleep the whole inter-cycle delay.  It now watches the
bridge's realtime cache during that sleep, so a stop/trail breach is acted on
within about a second -- *but* only after the broker's own mark agrees.  These
tests pin both the fast path and the guardrails (stale tick, unconfirmed mark,
no broker position, watch disabled).
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from typing import Any, Dict, List

from trader_core import Journal
from trader_core.config import KST
from trader_core.tests import support as tc_support

from runner.loop import MarketLoop
from runner.tests import support as rt_support


def _at(hour: int, minute: int = 0, day: int = 22) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=KST)


class _CountingEngine:
    """Records exit plans instead of placing them."""

    def __init__(self) -> None:
        self.plans: List[Any] = []

    def execute(self, plan):  # noqa: ANN001 - duck-typed plan
        self.plans.append(plan)
        return {"status": "ok"}


class TickWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tickwatch-")
        feed = rt_support.StaticFeed(rt_support.make_snapshot(["005930"]))
        self.cfg, self.loop, self.mock = rt_support.make_loop_bridge_and_feed(
            self.tmp, feed, log=lambda _line: None
        )
        self.loop.journal = Journal(os.path.join(self.tmp, "journal.sqlite3"))
        self.engine = _CountingEngine()
        self.loop.engine = self.engine  # type: ignore[assignment]
        # A held position: 100 shares at 10,000 with a 10 % stop.
        self.mock._positions["005930"] = {"qty": 100, "avg_price": 10000.0}
        self.mock.set_quote("005930", 10000.0)
        self.loop.journal.record_trade(
            side="buy", code="005930", qty=100, price=10000.0,
            stop_pct=0.10, tp_pct=0.10, ts=_at(9, 5), screener="rank_momentum",
        )

    def _tick_plans(self):
        return [p for p in self.engine.plans if "tick" in str(p.entry_reason)]

    def test_breach_in_the_cache_is_sold_immediately(self) -> None:
        # Cache and broker mark agree on -11 %: through the 10 % stop.
        self.mock.set_tick("005930", 8900.0)
        self.mock.set_quote("005930", 8900.0)
        report = self.loop.run_once(_at(10, 0))
        self.assertTrue(self.loop._tick_watch_pass(report))
        plans = self._tick_plans()
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual((plan.side, plan.code, plan.qty), ("sell", "005930", 100))
        self.assertIn("tick stop", plan.entry_reason)

    def test_a_stale_tick_does_not_sell(self) -> None:
        self.mock.set_tick("005930", 8900.0, ts=time.time() - 600)
        report = self.loop.run_once(_at(10, 0))
        self.assertFalse(self.loop._tick_watch_pass(report))
        self.assertEqual(self.engine.plans, [])

    def test_a_breach_the_broker_mark_does_not_confirm_is_ignored(self) -> None:
        self.mock.set_tick("005930", 8900.0)      # cache says -11 %
        self.mock.set_quote("005930", 10050.0)    # broker marks +0.5 %
        report = self.loop.run_once(_at(10, 0))
        self.assertFalse(self.loop._tick_watch_pass(report))
        self.assertEqual(self.engine.plans, [])

    def test_a_confirmed_breach_uses_the_broker_mark_too(self) -> None:
        self.mock.set_tick("005930", 8900.0)
        self.mock.set_quote("005930", 8800.0)     # broker agrees (worse)
        report = self.loop.run_once(_at(10, 0))
        self.assertTrue(self.loop._tick_watch_pass(report))
        plans = self._tick_plans()
        self.assertEqual(len(plans), 1)
        # The recorded trigger price is the worse of the two (the long's side).
        self.assertEqual(plans[0].algo["trigger_price"], 8800.0)

    def test_no_broker_position_means_no_sell(self) -> None:
        self.mock.set_tick("005930", 8900.0)
        self.mock._positions.clear()
        report = self.loop.run_once(_at(10, 0))
        self.assertFalse(self.loop._tick_watch_pass(report))
        self.assertEqual(self.engine.plans, [])

    def test_disabled_watch_does_not_touch_the_bridge(self) -> None:
        self.cfg.tick_watch_seconds = 0.0
        self.mock.set_tick("005930", 8900.0)
        report = self.loop.run_once(_at(10, 0))
        self.assertFalse(self.loop._tick_watch_pass(report))
        self.assertEqual(self.engine.plans, [])

    def test_watch_is_not_armed_outside_the_session(self) -> None:
        report = self.loop.run_once(_at(16, 0))  # after the close
        self.assertFalse(report.market_open)
        self.assertFalse(self.loop._tick_watch_armed(report))

    def test_watch_is_not_armed_while_halted(self) -> None:
        report = self.loop.run_once(_at(10, 0))
        report.halted = True
        self.assertFalse(self.loop._tick_watch_armed(report))

    def test_held_codes_come_from_the_journal(self) -> None:
        self.assertEqual(self.loop._tick_codes(), ["005930"])
        self.loop.journal.record_trade(
            side="buy", code="000660", qty=5, price=10000.0, ts=_at(9, 6),
        )
        self.assertEqual(sorted(self.loop._tick_codes()), ["000660", "005930"])

    def test_watched_wait_sends_the_exit_and_returns(self) -> None:
        """The inter-cycle wait polls the cache and returns once an exit is out."""
        self.mock.set_tick("005930", 8000.0)
        self.mock.set_quote("005930", 8000.0)
        self.cfg.tick_watch_seconds = 0.01
        report = self.loop.run_once(_at(10, 0))
        self.loop._sleep = lambda seconds: time.sleep(seconds)  # type: ignore[assignment]
        started = time.monotonic()
        self.loop._tick_watch_wait(3.0, report)  # would sleep 3 s
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)
        self.assertEqual(len(self._tick_plans()), 1)
        self.assertGreaterEqual(report.exits, 1)
        self.assertTrue(any("tick exit" in reason for reason in report.reasons))

    def test_a_broken_watch_keeps_the_cadence(self) -> None:
        """A failing cache read must not shorten the wait into fast cycles."""
        def _boom():
            raise RuntimeError("bridge hiccup")

        self.mock.cached_quotes = _boom  # type: ignore[assignment]
        self.cfg.tick_watch_seconds = 0.05
        report = self.loop.run_once(_at(10, 0))
        self.loop._sleep = lambda seconds: time.sleep(seconds)  # type: ignore[assignment]
        started = time.monotonic()
        self.loop._tick_watch_wait(0.6, report)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.5)
        self.assertEqual(self._tick_plans(), [])


if __name__ == "__main__":
    unittest.main()
