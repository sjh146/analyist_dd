"""Tests for the four risk reinforcements requested after the 2026-09-10 session.

1. Portfolio caps: concurrent codes + total invested ratio (Kelly sizes one bet,
   it says nothing about the sum).
2. Trailing stop: give back at most ``trailing_stop_pct`` from the peak once a
   trade is up ``trailing_arm_pct``; the high-water mark survives restarts.
3. Chase ceiling: never buy a name already up more than ``max_pct`` on the day.
4. Cold start: a thin-edge Kelly prior instead of an optimistic 0.55 (at b=1 a
   prior <= 0.50 makes Kelly itself say "do not bet").
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime

from trader_core import Config, Journal, MockBridge
from trader_core.config import KST
from trader_core.monitor import PositionMonitor
from trader_core.rankfeed import DEFAULT_PARAMS
from trader_core.risk import (
    check_invested_ratio,
    check_open_positions,
    kelly_fraction,
    risk_capped_qty,
    volatility_stop_pct,
)
from runner.rules import heat_blocks
NOW = datetime(2026, 9, 10, 14, 0, tzinfo=KST)


class PortfolioCapTest(unittest.TestCase):
    def test_open_position_cap(self) -> None:
        self.assertTrue(check_open_positions(9, 10))
        self.assertFalse(check_open_positions(10, 10))
        self.assertFalse(check_open_positions(11, 10))

    def test_zero_cap_means_unlimited(self) -> None:
        self.assertTrue(check_open_positions(999, 0))
        self.assertTrue(check_open_positions(999, -1))

    def test_unreadable_count_fails_closed(self) -> None:
        self.assertFalse(check_open_positions(None, 10))  # type: ignore[arg-type]

    def test_invested_ratio_allows_within_cap(self) -> None:
        # 1.7M already invested + 0.2M new on a 5M equity, 40 % cap = 2M
        self.assertTrue(check_invested_ratio(1_700_000, 200_000, 5_000_000, 0.40))

    def test_invested_ratio_blocks_past_cap(self) -> None:
        self.assertFalse(check_invested_ratio(1_900_000, 200_000, 5_000_000, 0.40))

    def test_invested_ratio_fails_closed_on_unknown_equity(self) -> None:
        self.assertFalse(check_invested_ratio(0.0, 200_000, 0.0, 0.40))

    def test_invested_ratio_disabled_at_zero_cap(self) -> None:
        self.assertTrue(check_invested_ratio(9_000_000, 200_000, 5_000_000, 0.0))


class ConfigDefaultsTest(unittest.TestCase):
    def test_portfolio_caps_have_conservative_defaults(self) -> None:
        cfg = Config()
        # 10 -> 5 (2026-09-25) -> 3 (2026-09-28) -> 5 (2026-10-07, 경로별 슬롯 배선)
        # -> 3 (2026-11-09, 사용자 지시 "최대 3종목 · 예수금 100%").
        # 이제 사이징은 equal_weight 이고, 종목당 1/3 · 총노출 1.0 이다.
        # 청산 규칙은 09-21·09-22 리플레이로 이미 전수 스윕되어 변경하지 않았다.
        self.assertEqual(cfg.max_open_positions, 3)
        self.assertAlmostEqual(cfg.max_invested_pct, 1.0)
        # 진입 상한은 실계좌 1일 회전 통제로 3건 유지(3종목 상한과 일치).
        self.assertEqual(cfg.daily_max_trades, 3)
        self.assertAlmostEqual(cfg.max_position_pct, 1.0 / 3.0, places=4)
        self.assertEqual(cfg.sizing_mode, "equal_weight")

    def test_trailing_defaults(self) -> None:
        """5.5 %/5 % since 2026-09-21 (see the Config comment for the measurement)."""
        cfg = Config()
        self.assertAlmostEqual(cfg.trailing_stop_pct, 0.05)
        self.assertAlmostEqual(cfg.trailing_arm_pct, 0.055)

    def test_armed_trailing_exit_is_never_a_loss(self) -> None:
        """A trailing stop protects a profit, so arming must imply a gain.

        Worst case for an armed trade is a peak exactly at the arm threshold:
        ``(1 + arm) x (1 - trail) - 1``.  With 3 %/5 % that was -2.15 % (the
        rule sold *below* entry), which is what the 2026-09-15 trailing exits
        measured (-3.0 .. +0.3 %, mean -1.7 %).  3 %/3 % made it -0.09 %
        (breakeven); 5.5 %/5 % makes it +0.22 %, so an armed exit is a gain by
        construction -- which is also what the 21-trade replay rewarded.
        """
        cfg = Config()
        worst = (1.0 + cfg.trailing_arm_pct) * (1.0 - cfg.trailing_stop_pct) - 1.0
        self.assertGreaterEqual(worst, 0.0, "an armed trailing exit must be a gain")
        self.assertAlmostEqual(worst, 0.00225, places=5)
        self.assertGreaterEqual(
            cfg.trailing_arm_pct,
            cfg.trailing_stop_pct,
            "the trail must sit inside the arm, not wider than it",
        )

    def test_cold_start_prior_is_thin_edge_not_optimistic(self) -> None:
        cfg = Config()
        self.assertAlmostEqual(cfg.default_win_rate, 0.53)
        # 0.53/1:1 -> f* = 0.06 -> 0.25 deploy = 1.5 % per position
        self.assertAlmostEqual(kelly_fraction(0.53, 1.0, 1.0) * 0.25, 0.015)

    def test_a_prior_at_or_below_half_disables_trading(self) -> None:
        """Why the cold start is 0.53 and not "0.45 to be safe"."""
        self.assertLessEqual(kelly_fraction(0.50, 1.0, 1.0), 0.0)
        self.assertLessEqual(kelly_fraction(0.45, 1.0, 1.0), 0.0)


class ChaseCeilingTest(unittest.TestCase):
    def test_ceiling_exists_and_is_paired_with_the_rank_window(self) -> None:
        """A ceiling is only meaningful if the rank window samples below it.

        Measured 2026-09-11: with ``rank_pct_to=30`` the ranking page is sorted
        by 상승률 descending, so its head sat at +18..30 % and the ceiling
        rejected every candidate for a whole morning.  Window <= ceiling is the
        invariant that keeps the shortlist inside the band we are willing to
        buy; a missing/zero ceiling means "buy the top of the move".
        """
        ceiling = DEFAULT_PARAMS["max_pct"]
        self.assertGreater(ceiling, 0.0)
        self.assertLessEqual(Config().rank_pct_to, ceiling)


class _MonitorTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.journal = Journal(os.path.join(self._tmp.name, "j.sqlite3"))
        self.trade_id = self.journal.record_trade(
            side="buy", code="005930", name="삼성전자", qty=100, price=10_000.0,
            screener="rank_momentum", stop_pct=0.07, tp_pct=0.15, ts=NOW,
        )

    def tearDown(self) -> None:
        self.journal.close()
        self._tmp.cleanup()

    def _monitor(self, price: float, **config_overrides) -> PositionMonitor:
        settings = dict(stop_loss_pct=0.07, take_profit_pct=0.15)
        settings.update(config_overrides)
        bridge = MockBridge(
            start_cash=1_000_000,
            positions={"005930": {"qty": 100, "avg_price": 10_000.0}},
            quotes={"005930": price},
        )
        return PositionMonitor(bridge, Config(**settings), self.journal)

    def _hwm(self) -> float:
        row = self.journal.open_trade_for("005930")
        return float(row.get("hwm_price") or 0.0)


class TrailingStopTest(_MonitorTestBase):
    def test_armed_trade_exits_after_giving_back_the_trail(self) -> None:
        first = self._monitor(11_000.0).heartbeat(now=NOW)
        self.assertEqual(first.exits, [])  # +10 %: armed, still fine
        self.assertAlmostEqual(self._hwm(), 11_000.0)

        second = self._monitor(10_400.0).heartbeat(now=NOW)  # <= 11,000 x 0.95
        self.assertEqual(len(second.exits), 1)
        self.assertIn("trailing stop", second.exits[0].reason)
        self.assertEqual(second.exits[0].qty, 100)
        exit_price = float(second.exits[0].price or 0.0)
        self.assertGreaterEqual(exit_price, 10_000.0)  # never a loss

    def test_armed_trade_exits_at_breakeven_not_below(self) -> None:
        """A peak right at the arm threshold still exits in profit.

        With the old 3 %/5 % pair this same peak exited at ``peak x 0.95`` =
        9,785, i.e. a +3 % trade became -2.15 % (the 2026-09-15 losses).  The
        current 5.5 %/5 % pair can only exit an armed trade at
        ``1.055 x 0.95 - 1 = +0.22 %`` or better.
        """
        self._monitor(10_550.0).heartbeat(now=NOW)  # +5.5 %: armed
        report = self._monitor(10_020.0).heartbeat(now=NOW)  # <= 10,550 x 0.95
        self.assertEqual(len(report.exits), 1)
        self.assertIn("trailing stop", report.exits[0].reason)
        self.assertGreater(float(report.exits[0].price or 0.0), 10_000.0)

    def test_peak_below_the_arm_rides_the_fixed_stop(self) -> None:
        """The price of the wider arm: a +4 % peak is no longer protected.

        At 3 %/3 % a trade that peeked at +4 % then fell back exited at
        ~breakeven; at 5.5 %/5 % it is not armed, so the trade keeps the full
        fixed stop.  That is the deliberate trade-off (the replay paid for it
        with 7 breakeven exits turning into take-profit hits), and it is pinned
        here so the behaviour is visible rather than surprising.
        """
        self._monitor(10_400.0).heartbeat(now=NOW)  # +4 % < arm 5.5 %: not armed
        back = self._monitor(10_010.0).heartbeat(now=NOW)  # back to entry
        self.assertEqual(back.exits, [], "no trail: the fixed stop has not fired")
        stopped = self._monitor(9_290.0).heartbeat(now=NOW)  # -7.1 %: fixed stop
        self.assertEqual(len(stopped.exits), 1)
        self.assertEqual(stopped.exits[0].reason, "stop")

    def test_not_armed_before_the_arm_threshold(self) -> None:
        self._monitor(10_200.0).heartbeat(now=NOW)  # +2 % < arm 5.5 %
        report = self._monitor(9_700.0).heartbeat(now=NOW)  # -3 %: no stop either
        self.assertEqual(report.exits, [])

    def test_high_water_mark_only_rises(self) -> None:
        self._monitor(11_000.0).heartbeat(now=NOW)
        self._monitor(10_700.0).heartbeat(now=NOW)  # above 11,000 x 0.95
        self.assertAlmostEqual(self._hwm(), 11_000.0)
        self._monitor(10_900.0).heartbeat(now=NOW)  # a new peak would raise it
        self.assertAlmostEqual(self._hwm(), 11_000.0)

    def test_disabled_when_trailing_pct_is_zero(self) -> None:
        self._monitor(11_000.0, trailing_stop_pct=0.0).heartbeat(now=NOW)
        report = self._monitor(10_400.0, trailing_stop_pct=0.0).heartbeat(now=NOW)
        self.assertEqual(report.exits, [])

    def test_fixed_stop_still_wins(self) -> None:
        report = self._monitor(9_000.0).heartbeat(now=NOW)  # -10 %
        self.assertEqual(len(report.exits), 1)
        self.assertEqual(report.exits[0].reason, "stop")


class _DivergentBridge:
    """Bridge double whose quote and broker mark deliberately disagree."""

    def __init__(self, position, quote_price):
        self._position = position
        self._quote = quote_price

    def positions(self):
        return [self._position]

    def quotes(self, codes):
        return {"005930": {"code": "005930", "current": self._quote}}


class QuoteMarkCrossCheckTest(_MonitorTestBase):
    """Guard 1: never stop out (or hold) on a price the broker disagrees with."""

    def _bridge(self, quote_price, mark_price):
        return _DivergentBridge(
            {
                "code": "005930",
                "name": "삼성전자",
                "qty": 100,
                "sellable_qty": 100,
                "avg_price": 5_400.0,
                "eval_amount": 100 * mark_price,
            },
            quote_price,
        )

    def test_uses_the_worse_price_when_they_disagree(self) -> None:
        # stop line = 5,400 x 0.93 = 5,022: quote 5,100 says "hold",
        # broker mark 4,900 says "stopped" -- the broker is right.
        bridge = self._bridge(quote_price=5_100.0, mark_price=4_900.0)
        report = PositionMonitor(bridge, Config(), self.journal).heartbeat(now=NOW)
        self.assertEqual(len(report.exits), 1)
        self.assertEqual(report.exits[0].reason, "stop")
        self.assertAlmostEqual(report.exits[0].price, 4_900.0)
        self.assertTrue(
            any("broker mark" in note for note in report.notes), report.notes
        )

    def test_guard_disabled_trusts_the_quote(self) -> None:
        bridge = self._bridge(quote_price=5_100.0, mark_price=4_900.0)
        monitor = PositionMonitor(
            bridge, Config(quote_divergence_pct=0.0), self.journal
        )
        report = monitor.heartbeat(now=NOW)
        self.assertEqual(report.exits, [])  # 5,100 is above the stop line
        self.assertFalse(any("broker mark" in note for note in report.notes))

    def test_small_divergence_keeps_the_quote(self) -> None:
        bridge = self._bridge(quote_price=4_930.0, mark_price=4_900.0)  # 0.6 %
        report = PositionMonitor(bridge, Config(), self.journal).heartbeat(now=NOW)
        self.assertFalse(any("broker mark" in note for note in report.notes))
        self.assertEqual(len(report.exits), 1)
        self.assertAlmostEqual(report.exits[0].price, 4_930.0)

    def test_missing_mark_is_not_an_error(self) -> None:
        bridge = _DivergentBridge(
            {"code": "005930", "qty": 100, "sellable_qty": 100, "avg_price": 5_400.0},
            5_100.0,
        )
        report = PositionMonitor(bridge, Config(), self.journal).heartbeat(now=NOW)
        self.assertEqual(report.exits, [])
        self.assertFalse(any("broker mark" in note for note in report.notes))


class LoopClockGuardTest(unittest.TestCase):
    """Guard 2: a skewed clock stops new entries (exits still run)."""

    class _Blocked:
        def blocks(self):
            return True

        def describe(self, _status=None):
            return "clock +14400.0s ahead of server time -> entries BLOCKED"

    class _Fine:
        def blocks(self):
            return False

        def describe(self, _status=None):
            return "clock +0.2s ahead of server time"

    def _run(self, tmp, guard):
        from runner.tests import support as rsupport

        snapshot = rsupport.make_snapshot(["005930"])
        _cfg, loop, _mock = rsupport.make_loop_bridge_and_feed(
            tmp, rsupport.StaticFeed(snapshot), codes=["005930"],
            log=lambda _line: None,
        )
        loop.clock_guard = guard
        report = loop.run_once(datetime(2026, 9, 7, 11, 0, tzinfo=KST))
        loop.journal.close()
        return report

    def test_skewed_clock_blocks_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp, self._Blocked())
            self.assertEqual(report.acted, 0)
            self.assertTrue(
                any("CLOCK" in reason for reason in report.reasons), report.reasons
            )

    def test_healthy_clock_keeps_trading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp, self._Fine())
            self.assertEqual(report.acted, 1, report.reasons)


class VolatilityStopTest(unittest.TestCase):
    """Stop distance scales with the name's own day range."""

    def test_wide_range_widens_the_stop(self) -> None:
        # 24 % day range x 0.5 = 12 % -> far outside the 7 % floor
        self.assertAlmostEqual(
            volatility_stop_pct(24.0, floor_pct=0.07, factor=0.5, cap_pct=0.20), 0.12
        )

    def test_floor_applies_to_calm_names(self) -> None:
        self.assertAlmostEqual(
            volatility_stop_pct(6.0, floor_pct=0.07, factor=0.5, cap_pct=0.20), 0.07
        )

    def test_cap_limits_absurd_stops(self) -> None:
        self.assertAlmostEqual(
            volatility_stop_pct(90.0, floor_pct=0.07, factor=0.5, cap_pct=0.20), 0.20
        )

    def test_missing_range_falls_back_to_the_floor(self) -> None:
        self.assertAlmostEqual(volatility_stop_pct(None, 0.07, 0.5, 0.20), 0.07)
        self.assertAlmostEqual(volatility_stop_pct(0.0, 0.07, 0.5, 0.20), 0.07)


class RiskCappedQtyTest(unittest.TestCase):
    """Equal-risk sizing: every position risks the same KRW."""

    def test_size_shrinks_with_a_wider_stop(self) -> None:
        tight = risk_capped_qty(10_000_000, 10_000, 0.07, 0.005)   # 50k/700
        wide = risk_capped_qty(10_000_000, 10_000, 0.12, 0.005)    # 50k/1200
        self.assertEqual(tight, 71)
        self.assertEqual(wide, 41)

    def test_budget_is_respected(self) -> None:
        qty = risk_capped_qty(5_000_000, 3_300, 0.15, 0.005)
        self.assertLessEqual(qty * 3_300 * 0.15, 5_000_000 * 0.005)

    def test_disabled_returns_zero(self) -> None:
        self.assertEqual(risk_capped_qty(10_000_000, 10_000, 0.07, 0.0), 0)
        self.assertEqual(risk_capped_qty(0, 10_000, 0.07, 0.005), 0)
        self.assertEqual(risk_capped_qty(10_000_000, 10_000, 0.0, 0.005), 0)


class BatchPortfolioCapTest(unittest.TestCase):
    """One cycle must not overshoot the portfolio caps.

    2026-09-11 10:43 live: the engine planned 3 buys while the account held 9
    codes against ``max_open_positions = 10``.  Every gate read the *same*
    broker snapshot, so each candidate individually saw a free slot and the
    account ended the cycle holding 12 codes.
    """

    def setUp(self) -> None:
        import shutil

        from trader_core.engine import TraderEngine
        from trader_core.feed import FeedSnapshot  # noqa: F401 - used in _snapshot
        from trader_core.tests import support as tc_support

        self.tc_support = tc_support
        self._shutil = shutil
        self._tmp = tempfile.mkdtemp(prefix="batch-cap-")
        self.journal = Journal(os.path.join(self._tmp, "j.sqlite3"))
        self.config = Config(
            market_hours_only=False,
            daytrading_requires_market_open=False,
            kill_switch_path=os.path.join(self._tmp, "kill.txt"),
            max_open_positions=3,
        )
        self.engine = TraderEngine(
            bridge=None, feed_client=None, journal=self.journal, config=self.config
        )

    def tearDown(self) -> None:
        self.journal.close()
        self._shutil.rmtree(self._tmp, ignore_errors=True)

    def _snapshot(self):
        from trader_core.feed import FeedSnapshot

        items = [
            self.tc_support.make_candidate(
                rank=index + 1,
                code="{0:06d}".format(100000 + index),
                name="종목{0}".format(index),
                close_price="10000.0",
            )
            for index in range(4)
        ]
        return FeedSnapshot.from_payload(self.tc_support.make_payload(close=items))

    def test_batch_stops_at_max_open_positions(self) -> None:
        from trader_core.engine import AccountState

        account = AccountState(
            cash=10_000_000,
            equity=10_000_000,
            positions=[
                {"code": "005930", "qty": 10, "avg_price": 10_000.0},
                {"code": "000660", "qty": 10, "avg_price": 10_000.0},
            ],
        )
        report = self.engine.evaluate(
            self._snapshot(),
            now=datetime(2026, 9, 4, 9, 30, tzinfo=KST),
            account=account,
            daily_count=0,
        )
        planned = [d for d in report.decisions if d.plan is not None]
        self.assertEqual(len(planned), 1)  # one free slot, four candidates
        blocked = [
            d.blocked_reason() or ""
            for d in report.decisions
            if d.plan is None
        ]
        self.assertTrue(
            any("planned this cycle" in reason for reason in blocked), blocked
        )


class VolatilitySizingEngineTest(unittest.TestCase):
    """End-to-end: a volatile candidate gets a wide stop and a small size."""

    def setUp(self) -> None:
        import shutil

        from trader_core import Config as EngineConfig, Journal
        from trader_core.engine import TraderEngine
        from trader_core.tests import support as tc_support

        self.tc_support = tc_support
        self._tmp = tempfile.mkdtemp(prefix="vol-sizing-")
        self.journal = Journal(os.path.join(self._tmp, "j.sqlite3"))

        class _Feed:
            def __init__(self, snapshot):
                self.snapshot = snapshot

            def fetch(self):
                return self.snapshot

        payload = tc_support.make_payload()
        # 24 % day range: stop becomes 12 %, i.e. 2.5 % of equity at risk
        payload["candidates"]["close"]["items"][0]["day_range_pct"] = "24.0"
        # a strong feed prior so Kelly alone would size ~100 shares
        payload["scoring_summary"]["screener_stats"] = {
            "close": {"win_rate": 60.0, "avg_win_pct": 8.0, "avg_loss_pct": 4.0,
                      "sample_count": 50}
        }
        from trader_core.feed import FeedSnapshot

        self.snapshot = FeedSnapshot.from_payload(payload)
        self.config = EngineConfig(
            market_hours_only=False,
            daytrading_requires_market_open=False,
            kill_switch_path=os.path.join(self._tmp, "kill.txt"),
            # 이 클래스는 Kelly + 동일위험 사이징 자체를 검증한다 —
            # 2026-11-09 기본값(equal_weight)이 아니라 그 경로를 고정한다.
            sizing_mode="kelly",
        )
        self.engine = TraderEngine(
            bridge=None, feed_client=_Feed(self.snapshot), journal=self.journal,
            config=self.config,
        )
        self._shutil = shutil

    def tearDown(self) -> None:
        self.journal.close()
        self._shutil.rmtree(self._tmp, ignore_errors=True)

    def _plan(self):
        from trader_core.engine import AccountState
        from trader_core.config import KST
        from datetime import datetime

        report = self.engine.evaluate(
            self.snapshot,
            now=datetime(2026, 9, 4, 9, 30, tzinfo=KST),
            account=AccountState(cash=10_000_000, equity=10_000_000),
            daily_count=0,
        )
        decision = report.decisions[0]
        self.assertTrue(decision.passed, decision.blocked_reason())
        return decision.plan

    def test_wide_stop_and_equal_risk_size(self) -> None:
        plan = self._plan()
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertAlmostEqual(plan.stop_pct, 0.12, places=6)
        # tp keeps the configured reward-risk multiple (7/7 => 1.0 since 2026-09-11)
        self.assertAlmostEqual(
            plan.tp_pct,
            0.12 * (self.config.take_profit_pct / self.config.stop_loss_pct),
            places=6,
        )
        # 0.5 % of 10M = 50,000 KRW risk / (10,000 x 0.12) = 41 shares
        self.assertEqual(plan.qty, 41)

    def test_risk_cap_binds_before_kelly(self) -> None:
        plan = self._plan()
        assert plan is not None
        self.assertLess(plan.qty, 100)  # Kelly alone would take 100


class HeatGaugeTest(unittest.TestCase):
    """R1's ceiling twin: a strong-but-late list must be blocked."""

    def _snapshot(self, scores, pcts, pct_key="pct_change"):
        from trader_core.feed import FeedSnapshot

        items = []
        for index, (score, pct) in enumerate(zip(scores, pcts), start=1):
            item = {
                "rank": index,
                "stock_code": "{0:06d}".format(5930 + index),
                "stock_name": "종목{0}".format(index),
                "close_price": "10000",
                "score": str(score),
                "reason": "test",
            }
            if pct is not None:
                item[pct_key] = str(pct)
            items.append(item)
        return FeedSnapshot.from_payload(
            {
                "generated_at": "2026-09-10T14:00:00+09:00",
                "candidates": {"rank_momentum": {"items": items}},
            }
        )

    def _config(self, **overrides):
        from runner.config import RunnerConfig

        return RunnerConfig(**overrides)

    def test_overheated_scores_are_blocked(self) -> None:
        snapshot = self._snapshot([97.0, 96.5, 98.0], [18.0, 20.0, 17.0])
        blocked = heat_blocks(snapshot, self._config())
        self.assertIn("rank_momentum", blocked)
        self.assertIn("heat", blocked["rank_momentum"])

    def test_cool_list_passes(self) -> None:
        snapshot = self._snapshot([60.0, 55.0, 58.0], [4.0, 6.0, 5.0])
        self.assertEqual(heat_blocks(snapshot, self._config()), {})

    def test_extended_pct_alone_blocks(self) -> None:
        # scores look modest (percentile score of a small set) but the list is
        # up 20 % on the day: that alone is a heat signal
        snapshot = self._snapshot([50.0, 40.0], [22.0, 19.0])
        blocked = heat_blocks(snapshot, self._config())
        self.assertIn("rank_momentum", blocked)
        self.assertIn("+", blocked["rank_momentum"])

    def test_screener_feed_field_name_also_blocks(self) -> None:
        # The screener feed names the same quantity ``day_change_pct``; reading
        # only ``pct_change`` left this ceiling silently inert on that feed
        # (measured 2026-09-30: close list averaging +20.2 % was not blocked).
        snapshot = self._snapshot([50.0, 40.0], [22.0, 19.0],
                                  pct_key="day_change_pct")
        blocked = heat_blocks(snapshot, self._config())
        self.assertIn("rank_momentum", blocked)
        self.assertIn("+", blocked["rank_momentum"])

    def test_cool_screener_feed_list_passes(self) -> None:
        snapshot = self._snapshot([60.0, 55.0], [4.0, 6.0],
                                  pct_key="day_change_pct")
        self.assertEqual(heat_blocks(snapshot, self._config()), {})

    def test_ceilings_can_be_disabled(self) -> None:
        snapshot = self._snapshot([99.0], [30.0])
        blocked = heat_blocks(
            snapshot, self._config(r1_max_avg_score=0.0, r1_max_avg_pct=0.0)
        )
        self.assertEqual(blocked, {})

    def test_defaults_are_the_measured_ceilings(self) -> None:
        from runner.config import RunnerConfig

        cfg = RunnerConfig()
        self.assertAlmostEqual(cfg.r1_max_avg_score, 95.0)
        self.assertAlmostEqual(cfg.r1_max_avg_pct, 15.0)


class LoopHeatBlockTest(unittest.TestCase):
    """The loop must treat a heat block exactly like an R1 block."""

    def test_heat_block_stops_entries(self) -> None:
        from runner.tests import support as rsupport
        from trader_core.config import KST

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = rsupport.make_snapshot(["005930"])
            for candidate in snapshot.candidates["close"]:
                candidate.score = 99.0  # hotter than the 95 ceiling
                candidate.extras["pct_change"] = "25.0"
            _cfg, loop, _mock = rsupport.make_loop_bridge_and_feed(
                tmp, rsupport.StaticFeed(snapshot), codes=["005930"],
                log=lambda _line: None,
            )
            report = loop.run_once(datetime(2026, 9, 7, 11, 0, tzinfo=KST))
            loop.journal.close()
        self.assertEqual(report.acted, 0)
        self.assertTrue(
            any("HEAT block" in reason for reason in report.reasons), report.reasons
        )
        # The state file's counter must include the heat block (R1 blocked
        # nothing here, so a count taken before the heat gauge reads 0).
        self.assertEqual(report.blocked_screeners, 1)


if __name__ == "__main__":
    unittest.main()
