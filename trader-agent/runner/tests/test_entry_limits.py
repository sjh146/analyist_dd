"""Tests for the daily entry caps and the anti-churn guard.

Cap history: the runner was raised from 3 to 100 for the paper account, then
brought back to 3 on the real-account switch (2026-09-28).  At 3 the cap is the
binding constraint on rotation/fees, and it matches the engine's
``daily_max_trades`` (two caps at different values only hide the real cause).
The anti-churn guard (``r2_one_entry_per_code_per_day``) still keeps a raised
cap meaning *different* names rather than the same names repeatedly.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime

from runner.config import RunnerConfig
from runner.tests import support as rsupport
from trader_core.config import KST, Config


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 7, hour, minute, tzinfo=KST)  # Monday


class EntryCapDefaultsTest(unittest.TestCase):
    def test_runner_daily_entry_cap_is_real_account_limit(self) -> None:
        self.assertEqual(RunnerConfig().r2_max_entries_per_day, 3)

    def test_engine_daily_trade_cap_matches_runner_cap(self) -> None:
        self.assertEqual(Config().daily_max_trades, 3)
        self.assertEqual(
            Config().daily_max_trades, RunnerConfig().r2_max_entries_per_day
        )

    def test_caps_are_per_config_instance(self) -> None:
        """A config file may still lower them (that is the tuning knob)."""
        cfg = RunnerConfig.from_mapping({"r2_max_entries_per_day": 5})
        self.assertEqual(cfg.r2_max_entries_per_day, 5)


class AntiChurnConfigTest(unittest.TestCase):
    def test_default_is_on(self) -> None:
        self.assertTrue(RunnerConfig().r2_one_entry_per_code_per_day)

    def test_can_be_disabled(self) -> None:
        cfg = RunnerConfig.from_mapping({"r2_one_entry_per_code_per_day": False})
        self.assertFalse(cfg.r2_one_entry_per_code_per_day)


class AntiChurnLoopTest(unittest.TestCase):
    """A code that already got an entry today must not be bought again."""

    def _run(self, tmp: str, *, seed: bool):
        snapshot = rsupport.make_snapshot(["005930"])
        _cfg, loop, _mock = rsupport.make_loop_bridge_and_feed(
            tmp,
            rsupport.StaticFeed(snapshot),
            codes=["005930"],
            log=lambda _line: None,
            r2_max=100,  # the cap must not be what blocks the second entry
        )
        if seed:
            loop.journal.record_trade(
                side="buy",
                code="005930",
                name="삼성전자",
                qty=1,
                price=10_000.0,
                screener="rank_momentum",
                ts=_at(9, 30),
            )
        report = loop.run_once(_at(11, 0))
        close = getattr(loop.journal, "close", None)
        if callable(close):
            close()  # Windows cannot remove the temp dir with sqlite open
        return report

    def test_without_prior_entry_the_candidate_is_bought(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp, seed=False)
            self.assertEqual(report.acted, 1, report.reasons)

    def test_second_entry_for_same_code_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp, seed=True)
            self.assertEqual(report.acted, 0)
            self.assertTrue(
                any("already entered today" in reason for reason in report.reasons),
                report.reasons,
            )

    def test_guard_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = rsupport.make_snapshot(["005930"])
            _cfg, loop, _mock = rsupport.make_loop_bridge_and_feed(
                tmp,
                rsupport.StaticFeed(snapshot),
                codes=["005930"],
                log=lambda _line: None,
                r2_max=100,
                r2_one_entry_per_code_per_day=False,
            )
            loop.journal.record_trade(
                side="buy", code="005930", qty=1, price=10_000.0, ts=_at(9, 30)
            )
            report = loop.run_once(_at(11, 0))
            loop.journal.close()
            self.assertEqual(report.acted, 1, report.reasons)


class CodesEnteredTodayTest(unittest.TestCase):
    """Journal helper that powers the guard."""

    def test_only_todays_buys_are_returned(self) -> None:
        from trader_core.journal import Journal

        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal("{0}/j.sqlite3".format(tmp))
            journal.record_trade(side="buy", code="005930", qty=1, price=1000.0,
                                 ts=_at(9, 10))                      # today, buy
            journal.record_trade(side="buy", code="A000660", qty=1, price=1000.0,
                                 ts=_at(9, 20))                      # today, buy (A form)
            journal.record_trade(side="sell", code="035420", qty=1, price=1000.0,
                                 ts=_at(9, 30))                      # today, sell
            journal.record_trade(side="buy", code="068270", qty=1, price=1000.0,
                                 ts=datetime(2026, 9, 4, 9, 0, tzinfo=KST))  # last week
            codes = sorted(journal.codes_entered_today(_at(11, 0)))
            journal.close()
        self.assertEqual(codes, ["000660", "005930"])

    def test_sells_do_not_block_a_re_entry(self) -> None:
        from trader_core.journal import Journal

        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal("{0}/j.sqlite3".format(tmp))
            journal.record_trade(side="sell", code="005930", qty=1, price=1000.0,
                                 ts=_at(9, 10))
            codes = journal.codes_entered_today(_at(11, 0))
            journal.close()
        self.assertEqual(codes, [])


if __name__ == "__main__":
    unittest.main()
