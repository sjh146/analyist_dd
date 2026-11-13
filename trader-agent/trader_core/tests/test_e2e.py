"""End-to-end dry-run: engine + MockBridge + real fixture feed, no network."""

from __future__ import annotations

import unittest
from datetime import timedelta

from trader_core import (
    Config,
    FeedSnapshot,
    Journal,
    MockBridge,
    PositionMonitor,
    ScreenerFeedClient,
    TraderEngine,
)
from trader_core.engine import OrderPlan

from trader_core.tests import support


class EndToEndDryRunTest(unittest.TestCase):
    """scan -> execute -> monitor exit -> close, all in-process."""

    def setUp(self) -> None:
        self.cfg = Config(
            market_hours_only=False,          # fixture is a post-close export
            daytrading_requires_market_open=False,
            kill_switch_path="__no_such_kill_switch__",
            daily_max_trades=20,
            dry_run=False,
        )
        self.mock = MockBridge(start_cash=50_000_000)
        self.journal = Journal(":memory:")
        feed = ScreenerFeedClient(support.FEED_FIXTURE)
        self.engine = TraderEngine(self.mock, feed, self.cfg, self.journal)

    def tearDown(self) -> None:
        self.journal.close()

    def test_scan_execute_monitor_close(self) -> None:
        snapshot = FeedSnapshot.from_text(support.read_feed_fixture())
        now = snapshot.generated_at
        report = self.engine.evaluate(snapshot, now=now)
        # real fixture: 20 close + 0 swing + 20 daytrading candidates
        self.assertEqual(len(report.decisions), 40)
        self.assertTrue(report.feed_fresh)
        self.assertTrue(report.actionable, "expected at least one actionable plan")
        self.assertEqual(
            len(self.journal.recent_decisions()),
            40,
            "every candidate decision must be journaled",
        )

        target = report.actionable[0]
        self.assertIsNotNone(target.plan)
        plan = target.plan
        assert plan is not None
        self.assertEqual(plan.side, "buy")
        cash_before = self.mock.cash

        filled = self.engine.execute(plan)
        self.assertEqual(filled["status"], "filled")
        self.assertLess(self.mock.cash, cash_before)
        open_rows = self.journal.open_trades()
        self.assertEqual(len(open_rows), 1)
        self.assertEqual(open_rows[0]["code"], plan.code)

        # crash the price to force a stop exit
        entry_price = float(open_rows[0]["price"])
        self.mock.set_quote(plan.code, round(entry_price * 0.9, 2))
        monitor = PositionMonitor(self.mock, self.cfg, self.journal)
        report_mon = monitor.heartbeat(now=now + timedelta(minutes=5))
        stops = [e for e in report_mon.exits if e.code == plan.code]
        self.assertEqual(len(stops), 1)
        exit_signal = stops[0]
        self.assertEqual(exit_signal.reason, "stop")

        sell_plan = OrderPlan(
            side=exit_signal.side,
            code=exit_signal.code,
            qty=exit_signal.qty,
            price=None,
            order_type=exit_signal.order_type,
            entry_reason="monitor stop",
        )
        sold = self.engine.execute(sell_plan)
        self.assertEqual(sold["status"], "filled")
        self.assertEqual(self.journal.open_trades(), [])
        summary = self.journal.summary(now=now + timedelta(days=1))
        self.assertEqual(summary["trades"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertLess(summary["total_pnl"], 0)
        # no position remains in the mock ledger
        self.assertEqual(self.mock.positions(), [])


if __name__ == "__main__":
    unittest.main()
