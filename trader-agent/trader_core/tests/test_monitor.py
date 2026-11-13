"""PositionMonitor heartbeat tests (stop/tp triggers, missing quotes hold)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from trader_core import (
    Config,
    Journal,
    MockBridge,
    PositionMonitor,
)

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 4, 10, 0, tzinfo=KST)


class MonitorTest(unittest.TestCase):
    def _monitor(self, bridge, journal=None) -> PositionMonitor:
        return PositionMonitor(
            bridge,
            Config(stop_loss_pct=0.07, take_profit_pct=0.15),
            journal,
        )

    def test_stop_loss_fires(self) -> None:
        mock = MockBridge(
            start_cash=10_000_000,
            positions={"005930": {"qty": 100, "avg_price": 10000.0}},
            quotes={"005930": 9000.0},  # -10% -> stop
        )
        report = self._monitor(mock).heartbeat(now=NOW)
        self.assertEqual(len(report.exits), 1)
        exit_signal = report.exits[0]
        self.assertEqual(exit_signal.code, "005930")
        self.assertEqual(exit_signal.reason, "stop")
        self.assertEqual(exit_signal.side, "sell")
        self.assertEqual(exit_signal.order_type, "market")
        self.assertEqual(exit_signal.qty, 100)
        self.assertEqual(exit_signal.price, 9000.0)

    def test_take_profit_fires(self) -> None:
        mock = MockBridge(
            start_cash=10_000_000,
            positions={"005930": {"qty": 100, "avg_price": 10000.0}},
            quotes={"005930": 12000.0},  # +20% -> tp
        )
        report = self._monitor(mock).heartbeat(now=NOW)
        self.assertEqual(len(report.exits), 1)
        self.assertEqual(report.exits[0].reason, "tp")

    def test_missing_quote_holds_with_note(self) -> None:
        mock = MockBridge(
            start_cash=10_000_000,
            positions={"005930": {"qty": 100, "avg_price": 10000.0}},
            quotes={},  # no quote at all
        )
        report = self._monitor(mock).heartbeat(now=NOW)
        self.assertEqual(report.exits, [])
        self.assertTrue(
            any("no current quote" in note for note in report.notes)
        )

    def test_sellable_qty_used(self) -> None:
        # A position dict-style entry in the mock uses full qty; here we pass
        # a raw positions-like object through the real mock ledger with a
        # partial sellable amount by selling first via the mock.
        mock = MockBridge(
            start_cash=10_000_000,
            positions={"005930": {"qty": 100, "avg_price": 10000.0}},
            quotes={"005930": 9000.0},
        )
        mock.order(side="sell", code="005930", qty=50, price=9000,
                   order_type="market")  # leaves 50 @ 10000 avg? no: mock
        # selling drops avg-price position qty to 50
        report = self._monitor(mock).heartbeat(now=NOW)
        self.assertEqual(report.exits[0].qty, 50)

    def test_journal_levels_override_defaults(self) -> None:
        journal = Journal(":memory:")
        journal.record_trade(
            side="buy", code="005930", qty=100, price=10000.0,
            stop_pct=0.05, tp_pct=0.30, ts=NOW,
        )
        mock = MockBridge(
            start_cash=10_000_000,
            positions={"005930": {"qty": 100, "avg_price": 10000.0}},
            quotes={"005930": 9500.0},  # -5% (fires only with journal stop)
        )
        report = self._monitor(mock, journal=journal).heartbeat(now=NOW)
        self.assertEqual(len(report.exits), 1)
        self.assertEqual(report.exits[0].reason, "stop")

    def test_no_positions(self) -> None:
        mock = MockBridge(start_cash=10_000_000)
        report = self._monitor(mock).heartbeat(now=NOW)
        self.assertEqual(report.exits, [])
        self.assertTrue(any("no open positions" in n for n in report.notes))

    def test_rank_momentum_dead_money_is_released_on_day_two(self) -> None:
        entry = datetime(2026, 9, 21, 11, 0, tzinfo=KST)  # Monday
        journal = Journal(":memory:")
        journal.record_trade(
            side="buy", code="005930", qty=100, price=10000.0,
            stop_pct=0.10, tp_pct=0.10, ts=entry, screener="rank_momentum",
        )
        mock = MockBridge(
            start_cash=10_000_000,
            positions={"005930": {"qty": 100, "avg_price": 10000.0}},
            quotes={"005930": 10100.0},  # +1%: no stop/tp, inside the 2% band
        )
        at = datetime(2026, 9, 22, 15, 15, tzinfo=KST)  # 2nd session, close window
        report = self._monitor(mock, journal=journal).heartbeat(now=at)
        self.assertEqual(len(report.exits), 1)
        self.assertIn("flat_stop", report.exits[0].reason)
        self.assertEqual(report.exits[0].qty, 100)

    def test_rank_momentum_working_position_is_left_alone(self) -> None:
        entry = datetime(2026, 9, 21, 11, 0, tzinfo=KST)
        journal = Journal(":memory:")
        journal.record_trade(
            side="buy", code="005930", qty=100, price=10000.0,
            stop_pct=0.10, tp_pct=0.10, ts=entry, screener="rank_momentum",
        )
        mock = MockBridge(
            start_cash=10_000_000,
            positions={"005930": {"qty": 100, "avg_price": 10000.0}},
            quotes={"005930": 10500.0},  # +5%: still working, hold it
        )
        at = datetime(2026, 9, 22, 15, 15, tzinfo=KST)
        report = self._monitor(mock, journal=journal).heartbeat(now=at)
        self.assertEqual(report.exits, [])


if __name__ == "__main__":
    unittest.main()
