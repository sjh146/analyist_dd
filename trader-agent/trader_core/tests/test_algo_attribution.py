"""Algorithm attribution: every trade must say which algorithm produced it.

Requirement (operator): the log/journal must record which algorithm was used for
a trade, *without* slowing trading down.  These tests pin the record's content
and prove the journal (not just the log) carries it.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime

from trader_core import Config, Journal
from trader_core.algo import (
    DECISION_VERSION,
    dumps,
    entry_record,
    exit_record,
    loads,
    merge,
    summary,
)
from trader_core.config import KST
from trader_core.engine import AccountState, OrderPlan, TraderEngine
from trader_core.feed import FeedSnapshot
from trader_core.tests import support as tc_support
from runner.tests import support as rsupport


class RecordTest(unittest.TestCase):
    def test_entry_record_shape(self) -> None:
        record = entry_record(
            screener="rank_momentum",
            scoring="percentile",
            params={"min_pct": 2.0, "max_pct": 12.0, "min_pullback_pct": 3.0,
                    "min_volume": 100000, "skip_limit_up": True, "top_n": 6,
                    "scoring": "percentile", "unrelated": 1},
            candidate={"rank": 5, "pct": 27.5, "score": 93.1,
                       "day_range_pct": 23.8, "off_high_pct": -1.9},
            sizing={"model": "kelly+risk-parity", "kelly_qty": 15,
                    "risk_qty": 41, "final_qty": 15},
            stop={"model": "vol-range", "stop_pct": 0.12, "tp_pct": 0.2571},
            exit_mode="stop_tp",
        )
        self.assertEqual(record["version"], DECISION_VERSION)
        self.assertEqual(record["screener"], "rank_momentum")
        self.assertEqual(record["scoring"], "percentile")
        self.assertEqual(record["filters"]["max_pct"], 12.0)
        self.assertNotIn("unrelated", record["filters"])
        self.assertEqual(record["candidate"]["rank"], 5)
        self.assertEqual(record["sizing"]["final_qty"], 15)
        self.assertEqual(record["stop"]["model"], "vol-range")
        self.assertEqual(record["exit_mode"], "stop_tp")
        self.assertEqual(record["side"], "buy")

    def test_exit_record_names_the_rule(self) -> None:
        record = exit_record(
            rule="trailing stop (-5% from peak 11,000)",
            trigger_price=10450.0,
            price_source="quote_or_broker_mark",
            qty=100,
        )
        self.assertEqual(record["side"], "sell")
        self.assertIn("trailing", record["rule"])
        self.assertEqual(record["trigger_price"], 10450.0)

    def test_dumps_and_loads_round_trip(self) -> None:
        record = entry_record("close", "absolute", {"min_pct": 1.0}, {}, {}, {})
        self.assertEqual(loads(dumps(record)), record)
        self.assertEqual(loads("not json"), {})
        self.assertEqual(loads(None), {})
        self.assertEqual(dumps({}), "{}")

    def test_merge_keeps_entry_fields(self) -> None:
        entry = entry_record("close", "percentile", {"max_pct": 12.0},
                             {"rank": 1}, {"final_qty": 3}, {"stop_pct": 0.07})
        merged = json.loads(merge(dumps(entry), exit_record("stop", 99.0)))
        self.assertEqual(merged["screener"], "close")   # entry kept
        self.assertEqual(merged["rule"], "stop")        # exit added

    def test_summary_is_one_ascii_line(self) -> None:
        record = entry_record(
            "rank_momentum", "percentile", {"max_pct": 12.0},
            {"rank": 5, "pct": 27.5, "score": 93.1, "day_range_pct": 23.8,
             "off_high_pct": -1.9},
            {"model": "kelly+risk-parity", "final_qty": 15, "kelly_qty": 15,
             "risk_qty": 41},
            {"model": "vol-range", "stop_pct": 0.12, "tp_pct": 0.2571},
        )
        text = summary(record)
        self.assertNotIn("\n", text)
        for token in ("screener=rank_momentum", "scoring=percentile",
                      "rank=5", "off_high=-1.9", "stop=vol-range 0.12"):
            self.assertIn(token, text)


class JournalColumnTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.journal = Journal(os.path.join(self._tmp.name, "j.sqlite3"))

    def tearDown(self) -> None:
        self.journal.close()
        self._tmp.cleanup()

    def test_algo_json_is_stored_and_returned(self) -> None:
        record = entry_record("close", "percentile", {"max_pct": 12.0},
                              {"rank": 1, "score": 90.0}, {"final_qty": 5},
                              {"model": "vol-range", "stop_pct": 0.1})
        self.journal.record_trade(
            side="buy", code="005930", qty=5, price=100.0,
            screener="close", algo_json=dumps(record),
        )
        row = self.journal.open_trade_for("005930")
        self.assertEqual(loads(row["algo_json"])["scoring"], "percentile")

    def test_close_merges_the_exit_record(self) -> None:
        record = entry_record("close", "percentile", {}, {"rank": 1}, {}, {})
        trade_id = self.journal.record_trade(
            side="buy", code="005930", qty=5, price=100.0,
            algo_json=dumps(record),
        )
        row = self.journal.open_trade_for("005930")
        self.journal.close_trade(
            trade_id=trade_id, exit_price=95.0, exit_reason="monitor stop",
            algo_patch=merge(row["algo_json"], exit_record("stop", 95.0)),
        )
        closed = self.journal.closed_trades(1)[0]
        stored = loads(closed["algo_json"])
        self.assertEqual(stored["screener"], "close")  # entry attribution kept
        self.assertEqual(stored["rule"], "stop")       # exit rule added

    def test_schema_is_v4(self) -> None:
        version = self.journal._conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 4)


class EngineAttributionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="algo-attrib-")
        self.journal = Journal(os.path.join(self._tmp, "j.sqlite3"))
        payload = tc_support.make_payload()
        item = payload["candidates"]["close"]["items"][0]
        item["day_range_pct"] = "24.0"
        item["day_high"] = "11000"
        item["pct_change"] = "17.5"
        payload["filters"] = {"min_pct": 2.0, "max_pct": 12.0,
                              "min_pullback_pct": 3.0, "scoring": "percentile"}
        self.snapshot = FeedSnapshot.from_payload(payload)
        self.engine = TraderEngine(
            bridge=None,
            feed_client=None,
            journal=self.journal,
            config=Config(
                market_hours_only=False,
                daytrading_requires_market_open=False,
                kill_switch_path=os.path.join(self._tmp, "kill.txt"),
                # 이 테스트는 Kelly 사이저의 기록(algo.sizing)을 검증한다.
                sizing_mode="kelly",
            ),
        )

    def tearDown(self) -> None:
        import shutil

        self.journal.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _plan(self) -> OrderPlan:
        report = self.engine.evaluate(
            self.snapshot,
            now=datetime(2026, 9, 4, 9, 30, tzinfo=KST),
            account=AccountState(cash=10_000_000, equity=10_000_000),
            daily_count=0,
        )
        decision = report.decisions[0]
        self.assertTrue(decision.passed, decision.blocked_reason())
        plan = decision.plan
        assert plan is not None
        return plan

    def test_plan_carries_the_algorithm_record(self) -> None:
        algo = self._plan().algo
        self.assertEqual(algo["screener"], "close")
        self.assertEqual(algo["scoring"], "percentile")
        self.assertEqual(algo["filters"]["max_pct"], 12.0)
        self.assertEqual(algo["candidate"]["day_range_pct"], 24.0)
        self.assertEqual(algo["candidate"]["pct"], 17.5)
        self.assertEqual(algo["sizing"]["model"], "kelly+risk-parity")
        self.assertIn("kelly_qty", algo["sizing"])
        self.assertIn("risk_qty", algo["sizing"])
        self.assertEqual(algo["stop"]["model"], "vol-range")
        self.assertAlmostEqual(algo["stop"]["stop_pct"], 0.12)

    def test_off_high_is_recorded(self) -> None:
        algo = self._plan().algo
        # day high 11,000 vs the 10,000 plan price -> -9.09 %
        self.assertAlmostEqual(algo["candidate"]["off_high_pct"], -9.0909, places=3)


class LoopAttributionTest(unittest.TestCase):
    """The loop must log and journal the attribution for every fill."""

    def test_fill_writes_attribution_to_journal_and_log(self) -> None:
        lines = []
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = rsupport.make_snapshot(["005930"])
            _cfg, loop, _mock = rsupport.make_loop_bridge_and_feed(
                tmp, rsupport.StaticFeed(snapshot), codes=["005930"],
                log=lines.append,
            )
            report = loop.run_once(datetime(2026, 9, 7, 11, 0, tzinfo=KST))
            self.assertEqual(report.acted, 1, report.reasons)
            rows = loop.journal.open_trades()
            loop.journal.close()

        self.assertEqual(len(rows), 1)
        stored = loads(rows[0]["algo_json"])
        self.assertEqual(stored["screener"], "close")
        self.assertEqual(stored["exit_mode"], "stop_tp")
        self.assertIn("entry_window", stored)
        self.assertTrue(stored["sizing"]["final_qty"] == rows[0]["qty"])

        algo_lines = [line for line in lines if line.startswith("ALGO ")]
        self.assertEqual(len(algo_lines), 1)
        self.assertIn("screener=close", algo_lines[0])


if __name__ == "__main__":
    unittest.main()
