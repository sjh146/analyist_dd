"""Tests for the self-tuning review step (:mod:`trader_core.review`).

The review step is the only place that changes strategy parameters, so these
tests pin its safety properties: small samples barely move anything, the entry
bar moves in bounded steps, and a system with no closed trades writes nothing.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from trader_core.journal import Journal
from trader_core.rankfeed import DEFAULT_R1_MIN_AVG_SCORE
from trader_core.review import (
    PRIOR_WIN_RATE,
    STRUCTURAL_SCORE_MEAN,
    THRESHOLD_MAX,
    load_params,
    performance,
    recommend,
    review,
    shrink,
    trade_return_pct,
)


def _closed(pnl, price=10000.0, qty=10):
    return {"pnl": pnl, "price": price, "qty": qty}


class TradeReturnTest(unittest.TestCase):
    def test_return_pct(self) -> None:
        self.assertAlmostEqual(trade_return_pct(_closed(1000)), 1.0)

    def test_loss_is_negative(self) -> None:
        self.assertAlmostEqual(trade_return_pct(_closed(-2500)), -2.5)

    def test_missing_fields_are_none(self) -> None:
        self.assertIsNone(trade_return_pct({"pnl": 100}))
        self.assertIsNone(trade_return_pct({"pnl": None, "price": 1, "qty": 1}))


class PerformanceTest(unittest.TestCase):
    def test_mixed_results(self) -> None:
        stats = performance([_closed(2000), _closed(-1000), _closed(3000), _closed(-500)])
        self.assertEqual(stats["trades"], 4)
        self.assertEqual(stats["wins"], 2)
        self.assertEqual(stats["losses"], 2)
        self.assertAlmostEqual(stats["win_rate"], 0.5)
        self.assertAlmostEqual(stats["avg_win_pct"], 2.5)
        self.assertAlmostEqual(stats["avg_loss_pct"], 0.75)
        self.assertAlmostEqual(stats["total_pnl"], 3500.0)
        self.assertAlmostEqual(stats["profit_factor"], 5000 / 1500)

    def test_no_losses_has_no_profit_factor(self) -> None:
        stats = performance([_closed(1000)])
        self.assertIsNone(stats["profit_factor"])

    def test_empty(self) -> None:
        stats = performance([])
        self.assertEqual(stats["trades"], 0)
        self.assertEqual(stats["win_rate"], 0.0)


class ShrinkTest(unittest.TestCase):
    def test_zero_samples_returns_prior(self) -> None:
        self.assertEqual(shrink(0.9, 0.55, 0), 0.55)

    def test_large_sample_dominates(self) -> None:
        self.assertGreater(shrink(0.9, 0.55, 1000), 0.85)

    def test_small_sample_stays_near_prior(self) -> None:
        value = shrink(0.9, 0.55, 3)
        self.assertLess(value, 0.65)
        self.assertGreater(value, 0.55)


class RecommendTest(unittest.TestCase):
    def test_below_min_samples_changes_nothing(self) -> None:
        params = {"r1_min_avg_score": {"rank_momentum": 55.0}}
        updated, rationale = recommend(params, {"trades": 2, "win_rate": 0.1})
        self.assertEqual(updated, params)
        self.assertIn("not enough closed trades", rationale[0])

    def test_weak_win_rate_tightens_the_entry_bar(self) -> None:
        stats = {"trades": 20, "win_rate": 0.30, "avg_win_pct": 1.0,
                 "avg_loss_pct": 1.0, "total_pnl": -5000.0}
        updated, rationale = recommend({"r1_min_avg_score": {"rank_momentum": 55.0}}, stats)
        self.assertEqual(updated["r1_min_avg_score"]["rank_momentum"], 60.0)
        self.assertTrue(any("tighten" in line for line in rationale))

    def test_healthy_win_rate_loosens_the_entry_bar(self) -> None:
        stats = {"trades": 30, "win_rate": 0.62, "avg_win_pct": 3.0,
                 "avg_loss_pct": 1.0, "total_pnl": 25000.0}
        updated, _ = recommend({"r1_min_avg_score": {"rank_momentum": 55.0}}, stats)
        self.assertEqual(updated["r1_min_avg_score"]["rank_momentum"], 52.5)

    def test_tightening_is_bounded(self) -> None:
        stats = {"trades": 20, "win_rate": 0.10, "avg_win_pct": 1.0,
                 "avg_loss_pct": 1.0, "total_pnl": -1.0}
        updated, _ = recommend({"r1_min_avg_score": {"rank_momentum": THRESHOLD_MAX}}, stats)
        self.assertEqual(updated["r1_min_avg_score"]["rank_momentum"], THRESHOLD_MAX)

    def test_tightening_stops_below_the_structural_mean(self) -> None:
        """A floor at the percentile mean is a permanent block, not a higher bar.

        Measured 2026-09-11: the rank shortlist's own average was 50.0 (scores
        84/58/50/44/40/24), so tightening the floor from 45 to 50 would have
        blocked the feed every cycle instead of demanding better candidates.
        """
        stats = {"trades": 20, "win_rate": 0.20, "avg_win_pct": 1.0,
                 "avg_loss_pct": 1.0, "total_pnl": -5000.0}
        updated, rationale = recommend(
            {"r1_min_avg_score": {"rank_momentum": 45.0}}, stats
        )
        floor = updated["r1_min_avg_score"]["rank_momentum"]
        self.assertLess(floor, STRUCTURAL_SCORE_MEAN)
        self.assertLessEqual(floor, 45.0)
        self.assertTrue(any("tighten" in line for line in rationale))

    def test_priors_are_shrunk_toward_conservative_values(self) -> None:
        stats = {"trades": 10, "win_rate": 0.9, "avg_win_pct": 8.0,
                 "avg_loss_pct": 8.0, "total_pnl": 1000.0}
        updated, _ = recommend({"r1_min_avg_score": {}}, stats)
        self.assertLess(updated["prior_win_rate"], 0.75)      # not 0.9
        self.assertGreater(updated["prior_win_rate"], PRIOR_WIN_RATE)
        # 10 observations vs 10 pseudo-samples -> halfway between the observed
        # 8 % and each prior (2.0 % win, 1.5 % loss).
        self.assertEqual(updated["prior_avg_win_pct"], 5.0)
        self.assertEqual(updated["prior_avg_loss_pct"], 4.75)


class ReviewCycleTest(unittest.TestCase):
    def _journal_with_trades(self, path, wins=3, losses=3):
        journal = Journal(path)
        self._ids = []
        for index in range(wins):
            trade_id = journal.record_trade(
                side="buy", code="005930", qty=10, price=10000.0,
                name="삼성전자", entry_reason="test",
            )
            journal.close_trade(trade_id, exit_price=10300.0, exit_reason="tp")
        for index in range(losses):
            trade_id = journal.record_trade(
                side="buy", code="000660", qty=10, price=10000.0,
                name="SK하이닉스", entry_reason="test",
            )
            journal.close_trade(trade_id, exit_price=9700.0, exit_reason="stop")
        return journal

    def test_no_closed_trades_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(os.path.join(tmp, "j.sqlite3"))
            params_path = os.path.join(tmp, "params.json")
            result = review(journal, params_path)
            self.assertEqual(result["stats"]["trades"], 0)
            self.assertEqual(result["written"], "")
            self.assertFalse(os.path.exists(params_path))
            journal.close()

    def test_cycle_writes_params_the_engine_and_loop_can_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = self._journal_with_trades(os.path.join(tmp, "j.sqlite3"))
            params_path = os.path.join(tmp, "params.json")
            result = review(journal, params_path, min_samples=5)
            journal.close()

            self.assertTrue(os.path.exists(params_path))
            written = load_params(params_path)
            self.assertEqual(written["reviewed_trades"], 6)
            self.assertIn("prior_win_rate", written)
            self.assertIn("prior_avg_win_pct", written)
            self.assertIn("r1_min_avg_score", written)
            self.assertIn("rank_momentum", written["r1_min_avg_score"])
            self.assertIn("generated_at", written)
            # 3 wins / 3 losses -> win rate 0.5 -> entry bar unchanged
            self.assertEqual(
                written["r1_min_avg_score"]["rank_momentum"],
                DEFAULT_R1_MIN_AVG_SCORE,
            )

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = self._journal_with_trades(os.path.join(tmp, "j.sqlite3"))
            params_path = os.path.join(tmp, "params.json")
            result = review(journal, params_path, apply=False)
            journal.close()
            self.assertEqual(result["written"], "")
            self.assertFalse(os.path.exists(params_path))
            self.assertEqual(result["stats"]["trades"], 6)

    def test_params_file_is_merged_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params_path = os.path.join(tmp, "params.json")
            with open(params_path, "w", encoding="utf-8") as handle:
                json.dump({"min_pct": 3.5, "top_n": 4}, handle)
            journal = self._journal_with_trades(os.path.join(tmp, "j.sqlite3"))
            review(journal, params_path, min_samples=3)
            journal.close()
            written = load_params(params_path)
            self.assertEqual(written["min_pct"], 3.5)  # hand-tuned filter kept
            self.assertEqual(written["top_n"], 4)


if __name__ == "__main__":
    unittest.main()
