"""Loop-level tests for per-screener entry windows and time-based exits.

These pin the behaviour that makes the ``analysit_dd`` lists safe to consume:

* a **종가 (close)** list must be bought into the closing auction, not at 09:05;
* a **swing** list may be bought any time during the session;
* a 종가 position is sold at the **next session's open**, even though no stop or
  take-profit was touched;
* a swing position is not dumped at the open.

Without these rules a close-to-open trade silently becomes a multi-day hold (or
a swing name gets liquidated at the bell for no reason).
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta

from trader_core.config import KST
from trader_core.journal import Journal
from trader_core.monitor import PositionMonitor

from runner.tests import support as rsupport

CLOSE_PROFILE = {"entry_window": "14:50-15:25", "exit_mode": "next_open"}
SWING_PROFILE = {"entry_window": "09:00-15:20", "exit_mode": "stop_tp"}


def _at(hour: int, minute: int = 0, day: int = 7) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=KST)


class _PositionBridge:
    """Minimal bridge double for the monitor (positions + quotes only)."""

    def __init__(self, positions, quotes) -> None:
        self._positions = positions
        self._quotes = quotes

    def positions(self):
        return list(self._positions)

    def quotes(self, codes):
        return {c: self._quotes[c] for c in codes if c in self._quotes}


def _position(code="005930", qty=10, avg=10_000.0):
    return {"code": code, "name": "삼성전자", "qty": qty, "avg_price": avg,
            "sellable_qty": qty}


class EntryWindowLoopTest(unittest.TestCase):
    def _run(self, tmp, now, profiles, screener="close"):
        """Run one cycle with the candidate list placed under *screener*."""
        item = rsupport.tc_support.make_candidate(
            rank=1, code="005930", close_price="10000", score="90.0"
        )
        payload = rsupport.tc_support.make_payload(
            generated_at="2026-09-07T08:55:00+09:00",
            close=[item] if screener == "close" else [],
            swing=[item] if screener == "swing" else [],
            daytrading=None,
            screener_stats=None,
        )
        from trader_core.feed import FeedSnapshot

        snapshot = FeedSnapshot.from_payload(payload)
        _cfg, loop, _mock = rsupport.make_loop_bridge_and_feed(
            tmp, rsupport.StaticFeed(snapshot), codes=["005930"],
            log=lambda _line: None, screener_profiles=profiles,
        )
        report = loop.run_once(now)
        close = getattr(loop.journal, "close", None)
        if callable(close):
            close()  # Windows cannot remove the temp dir with sqlite open
        return report

    def test_close_list_is_not_bought_at_the_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp, _at(9, 5), {"close": dict(CLOSE_PROFILE)})
            self.assertEqual(report.acted, 0)
            # 2026-09-29: 진입창 밖 후보는 엔진 슬롯을 낭비하지 않도록 평가 전에 걸러진다
            # (PRE filter ... window N). 사유는 어느 형태로든 남아야 한다.
            self.assertTrue(
                any("outside entry window" in reason or ("PRE filter" in reason and "window" in reason)
                    for reason in report.reasons),
                report.reasons,
            )

    def test_close_list_is_bought_into_the_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp, _at(15, 0), {"close": dict(CLOSE_PROFILE)})
            self.assertEqual(report.acted, 1)

    def test_swing_list_is_bought_mid_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(
                tmp, _at(11, 0), {"swing": dict(SWING_PROFILE)}, screener="swing"
            )
            self.assertEqual(report.acted, 1)


class TimeExitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.journal = Journal(f"{self._tmp.name}/j.sqlite3")

    def tearDown(self) -> None:
        self.journal.close()
        self._tmp.cleanup()

    def _monitor(self, screener: str, entry_ts: datetime, profiles):
        self.journal.record_trade(
            side="buy", code="005930", qty=10, price=10_000.0, name="삼성전자",
            screener=screener, stop_pct=0.07, tp_pct=0.15, ts=entry_ts,
        )
        bridge = _PositionBridge([_position()], {"005930": {"current": 10_000}})
        from trader_core.config import Config

        return PositionMonitor(
            bridge, Config(stop_loss_pct=0.07, take_profit_pct=0.15),
            self.journal, profiles=profiles,
        )

    def test_close_position_is_sold_at_the_next_open(self) -> None:
        monitor = self._monitor("close", _at(15, 5), {"close": dict(CLOSE_PROFILE)})
        report = monitor.heartbeat(now=_at(9, 3, day=8))
        self.assertEqual(len(report.exits), 1)
        self.assertIn("next_open", report.exits[0].reason)
        self.assertEqual(report.exits[0].qty, 10)

    def test_close_position_is_kept_until_the_open(self) -> None:
        monitor = self._monitor("close", _at(15, 5), {"close": dict(CLOSE_PROFILE)})
        same_day = monitor.heartbeat(now=_at(15, 20))
        self.assertEqual(same_day.exits, [])
        # ... and not at 10:00 the next day either (outside the open window)
        later = monitor.heartbeat(now=_at(10, 0, day=8))
        self.assertEqual(later.exits, [])

    def test_swing_position_is_not_dumped_at_the_open(self) -> None:
        monitor = self._monitor("swing", _at(15, 5), {"swing": dict(SWING_PROFILE)})
        report = monitor.heartbeat(now=_at(9, 3, day=8))
        self.assertEqual(report.exits, [])

    def test_untagged_positions_keep_stop_tp_behaviour(self) -> None:
        """Old rows (no screener) must not suddenly become close-to-open."""
        monitor = self._monitor("", _at(15, 5), {})
        report = monitor.heartbeat(now=_at(9, 3, day=8))
        self.assertEqual(report.exits, [])


if __name__ == "__main__":
    unittest.main()
