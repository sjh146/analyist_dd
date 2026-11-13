"""Engine gate pipeline tests (all-pass, per-gate failure, kill switch, plans)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from trader_core import AccountState, Config, FeedSnapshot, Journal, MockBridge
from trader_core.engine import (
    DecisionReport,
    GateResult,
    OrderBlockedError,
    OrderPlan,
    TraderEngine,
    market_is_open,
)

from trader_core.tests import support

KST = timezone(timedelta(hours=9))
FRIDAY_OPEN = datetime(2026, 9, 4, 10, 0, tzinfo=KST)
FRIDAY_AFTER_CLOSE = datetime(2026, 9, 4, 20, 0, tzinfo=KST)
SATURDAY = datetime(2026, 9, 5, 10, 0, tzinfo=KST)


class MarketHoursTest(unittest.TestCase):
    def test_open_session(self) -> None:
        self.assertTrue(market_is_open(FRIDAY_OPEN))
        self.assertTrue(market_is_open(datetime(2026, 9, 4, 9, 0, tzinfo=KST)))
        self.assertTrue(market_is_open(datetime(2026, 9, 4, 15, 29, tzinfo=KST)))

    def test_closed_sessions(self) -> None:
        self.assertFalse(market_is_open(datetime(2026, 9, 4, 8, 59, tzinfo=KST)))
        self.assertFalse(market_is_open(datetime(2026, 9, 4, 15, 30, tzinfo=KST)))
        self.assertFalse(market_is_open(SATURDAY))  # weekend
        self.assertFalse(market_is_open(datetime(2026, 9, 6, 10, 0, tzinfo=KST)))  # Sun

    def test_naive_now_assumed_kst(self) -> None:
        naive = datetime(2026, 9, 4, 10, 0)
        self.assertTrue(market_is_open(naive))


class TraderEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="trader-core-test-")
        self.kill_path = os.path.join(self._tmp, "kill_switch.txt")
        self.cfg = Config(
            market_hours_only=False,
            daytrading_requires_market_open=False,
            kill_switch_path=self.kill_path,
            daily_max_trades=5,
            dry_run=False,
            # 게이트 파이프라인 테스트는 Kelly 기준 수량을 고정해 왔다.
            sizing_mode="kelly",
        )
        self.bridge = MockBridge(start_cash=10_000_000)
        # 경로 슬롯 게이트(_slot_allows)는 저널이 없으면 fail-closed 로 전 후보를
        # 막는다(2026-10-07 슬롯 배선) — 통과를 기대하는 테스트들은 빈 저널을 준다.
        self.engine = TraderEngine(
            self.bridge,
            feed_client=None,
            config=self.cfg,
            journal=Journal(":memory:"),
        )
        self.account = AccountState(cash=10_000_000, equity=10_000_000)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _snapshot(self, payload: dict) -> FeedSnapshot:
        return FeedSnapshot.from_payload(payload)

    def _eval(self, payload: dict, now=FRIDAY_OPEN, account=None, daily_count=0):
        return self.engine.evaluate(
            self._snapshot(payload),
            now=now,
            account=account if account is not None else self.account,
            daily_count=daily_count,
        )

    def test_all_gates_pass_produces_plan(self) -> None:
        payload = support.make_payload()
        report = self._eval(payload)
        self.assertIsInstance(report, DecisionReport)
        self.assertTrue(report.feed_fresh)
        self.assertTrue(report.market_open)
        self.assertEqual(len(report.decisions), 1)  # close(1) + swing(0) + day(0)
        decision = report.decisions[0]
        self.assertTrue(decision.passed, decision.blocked_reason())
        plan = decision.plan
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.side, "buy")
        self.assertEqual(plan.code, "005930")
        self.assertEqual(plan.order_type, "limit")
        self.assertEqual(plan.price, 10000.0)
        # cold-start priors 0.53/1/1 -> f*=0.06, deploy 1.0 -> 6% of 10M = 600k
        # (kelly_fraction 0.25 -> 1.0 on 2026-09-28: 실계좌 53만원에서 1.5% 로는
        #  10,000원 넘는 종목이 1주도 안 사져서 deploy 를 1.0 으로 올렸다)
        self.assertEqual(plan.qty, 60)
        self.assertEqual([g.gate for g in decision.gates], list(("feed_fresh",
            "kill_switch", "market_hours", "daily_trade_count",
            "balance_position_amount", "signal_sane")))
        self.assertTrue(all(g.ok for g in decision.gates))

    def test_stale_feed_blocks(self) -> None:
        payload = support.make_payload()
        # Feed export lands ~19:30; the default 24h budget must still reject a
        # payload older than one full day (e.g. weekend leftovers on Monday).
        report = self._eval(payload, now=FRIDAY_OPEN + timedelta(hours=25))
        self.assertFalse(report.feed_fresh)
        self.assertFalse(report.actionable)
        self.assertIn("feed_fresh", report.decisions[0].blocked_reason())

    def test_scoring_stats_percent_prior_drives_kelly_size(self) -> None:
        """BC-250 screener_stats win_rate is a percentage (60.0) and returns
        are avg_win_pct/avg_loss_pct — must map to p=0.6, b=8/4=2 => f*=0.4,
        deploy 0.25 => 10% of 10M equity = 1M / 10000 = 100 shares.

        The equal-risk cap is switched off here so this test isolates the Kelly
        path; ``test_risk_reinforcements`` covers the cap itself.
        """
        payload = support.make_payload()
        payload.setdefault("scoring_summary", {})["screener_stats"] = {
            "close": {
                "win_rate": 60.0,
                "avg_win_pct": 8.0,
                "avg_loss_pct": 4.0,
                "sample_count": 50,
            }
        }
        engine = TraderEngine(
            bridge=self.bridge,
            feed_client=None,
            journal=Journal(":memory:"),  # 슬롯 게이트 fail-closed: 빈 저널 주입
            config=Config(
                market_hours_only=False,
                daytrading_requires_market_open=False,
                kill_switch_path=self.kill_path,
                risk_per_trade_pct=0.0,
                # 이 테스트는 Kelly 경로(종목당 10% 상한)의 수량을 고정한다.
                sizing_mode="kelly",
                max_position_pct=0.10,
            ),
        )
        report = engine.evaluate(
            self._snapshot(payload), now=FRIDAY_OPEN, account=self.account,
            daily_count=0,
        )
        decision = report.decisions[0]
        self.assertTrue(decision.passed, decision.blocked_reason())
        plan = decision.plan
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.qty, 100)
        self.assertIsNotNone(plan.kelly_f)
        self.assertAlmostEqual(plan.kelly_f, 0.1, places=6)

    def test_kill_switch_toggles(self) -> None:
        payload = support.make_payload()
        with open(self.kill_path, "w", encoding="utf-8") as handle:
            handle.write("manual halt\n")
        report = self._eval(payload)
        self.assertTrue(report.kill_switch)
        self.assertFalse(report.actionable)
        self.assertIn("kill_switch", report.decisions[0].blocked_reason())
        os.remove(self.kill_path)
        report2 = self._eval(payload)
        self.assertFalse(report2.kill_switch)
        self.assertTrue(report2.actionable)

    def test_market_hours_gate_blocks_when_enabled(self) -> None:
        cfg = Config(market_hours_only=True, kill_switch_path=self.kill_path)
        engine = TraderEngine(self.bridge, None, cfg)
        payload = support.make_payload(generated_at=FRIDAY_AFTER_CLOSE.isoformat())
        report = engine.evaluate(
            self._snapshot(payload), now=FRIDAY_AFTER_CLOSE, account=self.account
        )
        self.assertFalse(report.actionable)
        self.assertIn("market_hours", report.decisions[0].blocked_reason())

    def test_daytrading_requires_live_session(self) -> None:
        cfg = Config(
            market_hours_only=False,
            daytrading_requires_market_open=True,
            kill_switch_path=self.kill_path,
        )
        engine = TraderEngine(
            self.bridge, None, cfg,
            Journal(":memory:"),  # 슬롯 게이트 fail-closed: close 통과 기대분 빈 저널
        )
        payload = support.make_payload(
            generated_at=FRIDAY_AFTER_CLOSE.isoformat(),
            close=[support.make_candidate(code="005930")],
            daytrading=[support.make_candidate(code="047080")],
        )
        report = engine.evaluate(
            self._snapshot(payload), now=FRIDAY_AFTER_CLOSE, account=self.account
        )
        by_screener = {d.screener: d for d in report.decisions}
        self.assertFalse(by_screener["daytrading"].passed)
        self.assertIn("market_hours", by_screener["daytrading"].blocked_reason())
        self.assertTrue(by_screener["close"].passed)

    def test_daily_count_cap_blocks(self) -> None:
        payload = support.make_payload()
        report = self._eval(payload, daily_count=5)
        self.assertFalse(report.actionable)
        self.assertIn("daily_trade_count", report.decisions[0].blocked_reason())

    def test_insufficient_cash_blocks(self) -> None:
        payload = support.make_payload()
        report = self._eval(payload, account=AccountState(cash=0, equity=10_000_000))
        self.assertFalse(report.actionable)
        self.assertIn(
            "balance_position_amount", report.decisions[0].blocked_reason()
        )

    def test_position_limit_blocks(self) -> None:
        payload = support.make_payload(close=[support.make_candidate(
            code="005930", close_price="1000.0", score="90.0")])
        account = AccountState(
            cash=1_000_000,
            equity=1_000_000,
            # 400주 = 원가 40만원 = equity 의 40% → 종목당 상한(1/3) 초과
            positions=[{"code": "005930", "qty": 400, "avg_price": 1000.0}],
        )
        report = self._eval(payload, account=account)
        self.assertFalse(report.actionable)
        self.assertIn(
            "balance_position_amount", report.decisions[0].blocked_reason()
        )

    def test_signal_sanity_blocks_low_score(self) -> None:
        payload = support.make_payload(
            close=[support.make_candidate(code="005930", score="0.0")]
        )
        report = self._eval(payload)
        self.assertFalse(report.actionable)
        self.assertIn("signal_sane", report.decisions[0].blocked_reason())

    def test_decision_journaling(self) -> None:
        journal = Journal(":memory:")
        engine = TraderEngine(self.bridge, None, self.cfg, journal)
        payload = support.make_payload(
            close=[support.make_candidate(code="005930", rank=1),
                   support.make_candidate(code="000660", rank=2)],
        )
        engine.evaluate(
            self._snapshot(payload), now=FRIDAY_OPEN, account=self.account
        )
        self.assertEqual(len(journal.recent_decisions()), 2)
        row = journal.recent_decisions()[0]
        self.assertEqual(row["code"], "000660")
        self.assertIn("feed_fresh", row["gates_json"])

    def test_liquidation_plans(self) -> None:
        positions = [
            {"code": "005930", "name": "S", "qty": 100, "sellable_qty": 100,
             "avg_price": 10000},
            {"code": "000660", "qty": 5, "sellable_qty": 0},
        ]
        plans = self.engine.liquidation_plans(positions, reason="kill-switch")
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.side, "sell")
        self.assertEqual(plan.code, "005930")
        self.assertEqual(plan.order_type, "market")
        self.assertEqual(plan.qty, 100)
        self.assertEqual(plan.entry_reason, "kill-switch")


class TraderEngineExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="trader-core-exec-")
        self.kill_path = os.path.join(self._tmp, "kill_switch.txt")
        self.cfg = Config(
            market_hours_only=False,
            daytrading_requires_market_open=False,
            kill_switch_path=self.kill_path,
            daily_max_trades=5,
            dry_run=False,
            # 게이트 파이프라인 테스트는 Kelly 기준 수량을 고정해 왔다.
            sizing_mode="kelly",
        )
        self.mock = MockBridge(start_cash=10_000_000)
        self.journal = Journal(":memory:")
        self.engine = TraderEngine(self.mock, None, self.cfg, self.journal)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)
        self.journal.close()

    def test_execute_buy_and_close_roundtrip(self) -> None:
        plan = OrderPlan(
            side="buy", code="005930", qty=25, price=10000.0,
            order_type="limit", entry_reason="test", stop_pct=0.07, tp_pct=0.15,
        )
        filled = self.engine.execute(plan)
        self.assertEqual(filled["status"], "filled")
        self.assertLess(self.mock.cash, 10_000_000)
        trades = self.journal.open_trades()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["code"], "005930")
        self.assertEqual(trades[0]["price"], 10000.0)

        self.mock.set_quote("005930", 11000.0)
        sell = OrderPlan(
            side="sell", code="005930", qty=25, price=None,
            order_type="market", entry_reason="tp",
        )
        result = self.engine.execute(sell)
        self.assertEqual(result["price"], 11000)
        self.assertEqual(self.journal.open_trades(), [])
        closed = self.journal.open_trades()
        self.assertEqual(closed, [])
        summary = self.journal.summary()
        self.assertEqual(summary["trades"], 1)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["total_pnl"], 25 * 1000.0)

    def test_market_order_without_reference_raises(self) -> None:
        plan = OrderPlan(
            side="sell", code="999999", qty=1, price=None, order_type="market"
        )
        with self.assertRaises(OrderBlockedError):
            self.engine.execute(plan)


if __name__ == "__main__":
    unittest.main()
