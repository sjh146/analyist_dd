"""R1-R4 pure rule helper tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from trader_core import Config, FeedSnapshot
from trader_core.config import KST

from trader_core.tests import support as tc_support

from runner import RunnerConfig
from runner.rules import (
    apply_r1,
    apply_r2,
    gate_blocked_codes,
    r3_deviation_ok,
    r3_reason,
    r4_triggered,
    stale_candidates,
    top_n_average,
)

KST_TZ = timezone(timedelta(hours=9))


def _snapshot_with_scores(scores, screener="close") -> FeedSnapshot:
    items = [
        tc_support.make_candidate(
            rank=i + 1, code="00000{0}".format(i + 1), score="{0}".format(score)
        )
        for i, score in enumerate(scores)
    ]
    if screener == "close":
        payload = tc_support.make_payload(close=items, swing=None)
    else:
        payload = tc_support.make_payload(swing=items, close=None)
    return FeedSnapshot.from_payload(payload)


class TopNAverageTest(unittest.TestCase):
    def test_empty_list_returns_none(self) -> None:
        snapshot = _snapshot_with_scores([])
        self.assertIsNone(top_n_average(snapshot.candidates["close"], 10))

    def test_missing_scores_ignored(self) -> None:
        snapshot = tc_support.make_payload(close=[
            tc_support.make_candidate(rank=1, code="111111", score=""),
            tc_support.make_candidate(rank=2, code="222222", score="90.0"),
        ])
        snap = FeedSnapshot.from_payload(snapshot)
        self.assertAlmostEqual(top_n_average(snap.candidates["close"], 10), 90.0)

    def test_fewer_than_n_averages_all(self) -> None:
        snapshot = _snapshot_with_scores([80.0, 90.0])
        self.assertAlmostEqual(top_n_average(snapshot.candidates["close"], 10), 85.0)

    def test_top_n_used_not_full_list(self) -> None:
        snapshot = _snapshot_with_scores([100.0, 90.0, 1.0, 1.0])
        self.assertAlmostEqual(top_n_average(snapshot.candidates["close"], 2), 95.0)


class R1Test(unittest.TestCase):
    def _cfg(self, **overrides) -> RunnerConfig:
        return RunnerConfig(**overrides)

    def test_high_average_not_blocked(self) -> None:
        cfg = self._cfg()
        snapshot = _snapshot_with_scores([90.0, 90.0])
        self.assertEqual(apply_r1(snapshot, cfg), {})

    def test_low_average_blocks_screener(self) -> None:
        cfg = self._cfg()
        snapshot = _snapshot_with_scores([95.0, 80.0])  # avg 87.5 < 88
        blocked = apply_r1(snapshot, cfg)
        self.assertIn("close", blocked)
        self.assertIn("r1: avg score 87.5 < 88.0", blocked["close"])

    def test_empty_screener_skipped(self) -> None:
        cfg = self._cfg()
        # Only swing carries candidates here; swing threshold (75) is easily met.
        snapshot = _snapshot_with_scores([90.0], screener="swing")
        blocked = apply_r1(snapshot, cfg)
        self.assertEqual(blocked, {})  # close has no candidates -> skipped

    def test_screener_without_threshold_skipped(self) -> None:
        cfg = self._cfg(r1_min_avg_score={"swing": 75.0})
        snapshot = _snapshot_with_scores([10.0])  # close not in thresholds map
        self.assertEqual(apply_r1(snapshot, cfg), {})

    def test_top_n_regime_not_tail_average(self) -> None:
        cfg = self._cfg(r1_top_n=2)
        snapshot = _snapshot_with_scores([95.0, 90.0, 5.0, 5.0])
        # top-2 average 92.5 >= 88 => no block despite tail collapse
        self.assertEqual(apply_r1(snapshot, cfg), {})


class R1ProbabilityScaleTest(unittest.TestCase):
    """score_kind=calibrated_prob 이면 확률 스케일 문턱을 쓴다.

    2026-09-29 실측: swing 점수는 모델 확률(0.5038~0.6245)인데 문턱은 0~100 스케일
    75 였다 → 상위10 평균 55.0 < 75 로 swing 경로가 구조적으로 닫혀 있었다.
    """

    def _snapshot(self, scores, kind="calibrated_prob", screener="swing"):
        items = [
            tc_support.make_candidate(
                rank=i + 1, code="00000{0}".format(i + 1),
                score="{0}".format(score), score_kind=kind,
                signal_date="2026-09-29",
            )
            for i, score in enumerate(scores)
        ]
        payload = (tc_support.make_payload(close=items, swing=None)
                   if screener == "close"
                   else tc_support.make_payload(swing=items, close=None))
        return FeedSnapshot.from_payload(payload)

    def test_probability_scale_uses_prob_threshold(self) -> None:
        cfg = RunnerConfig()
        blocked = apply_r1(self._snapshot([62.45, 50.38]), cfg)  # avg 0.564 < 0.58
        self.assertIn("swing", blocked)
        self.assertIn("avg prob 0.564 < 0.58", blocked["swing"])
        self.assertIn("calibrated", blocked["swing"])

    def test_probability_scale_passes_above_threshold(self) -> None:
        cfg = RunnerConfig()
        self.assertEqual(apply_r1(self._snapshot([70.0, 60.0]), cfg), {})  # avg 0.65

    def test_legacy_feed_without_score_kind_uses_score_threshold(self) -> None:
        cfg = RunnerConfig()
        blocked = apply_r1(self._snapshot([62.45, 50.38], kind=""), cfg)
        self.assertIn("avg score 56.4 < 75.0", blocked["swing"])

    def test_declared_screener_scale_uses_score_threshold(self) -> None:
        cfg = RunnerConfig()
        blocked = apply_r1(self._snapshot([62.45, 50.38], kind="screener"), cfg)
        self.assertIn("avg score", blocked["swing"])

    def test_disabled_prob_map_falls_back_to_score_scale(self) -> None:
        cfg = RunnerConfig(r1_min_avg_prob={})
        blocked = apply_r1(self._snapshot([62.45, 50.38]), cfg)
        self.assertIn("avg score", blocked["swing"])


class StaleCandidateTest(unittest.TestCase):
    """후보별 신선도: valid_until 과 signal_date 나이(둘 중 하나라도 지나면 stale)."""

    NOW = datetime(2026, 9, 29, 9, 0, tzinfo=KST_TZ)

    def _snapshot(self, **kwargs):
        item = tc_support.make_candidate(rank=1, code="123456", **kwargs)
        return FeedSnapshot.from_payload(
            tc_support.make_payload(close=[item], swing=None)
        )

    def test_old_signal_date_is_stale(self) -> None:
        cfg = RunnerConfig()  # close 상한 4일
        snapshot = self._snapshot(signal_date="2026-09-23")
        stale = stale_candidates(snapshot, cfg, self.NOW)
        self.assertIn("123456", stale)
        self.assertIn("6d old", stale["123456"])

    def test_recent_signal_date_kept(self) -> None:
        cfg = RunnerConfig()
        snapshot = self._snapshot(signal_date="2026-09-28")
        self.assertEqual(stale_candidates(snapshot, cfg, self.NOW), {})

    def test_zero_limit_disables_age_check(self) -> None:
        cfg = RunnerConfig(candidate_max_age_days={"close": 0})
        snapshot = self._snapshot(signal_date="2026-09-01")
        self.assertEqual(stale_candidates(snapshot, cfg, self.NOW), {})

    def test_valid_until_in_the_past_is_stale(self) -> None:
        cfg = RunnerConfig()
        snapshot = self._snapshot(
            signal_date="2026-09-29", valid_until="2026-09-29T08:00:00+09:00"
        )
        stale = stale_candidates(snapshot, cfg, self.NOW)
        self.assertIn("valid_until", stale["123456"])

    def test_valid_until_in_the_future_but_old_signal_is_stale(self) -> None:
        cfg = RunnerConfig()  # 두 상한 중 하나만 걸려도 차단
        snapshot = self._snapshot(
            signal_date="2026-09-20", valid_until="2026-10-30T15:30:00+09:00"
        )
        self.assertIn("123456", stale_candidates(snapshot, cfg, self.NOW))

    def test_missing_dates_are_kept(self) -> None:
        cfg = RunnerConfig()
        snapshot = self._snapshot(signal_date="")
        self.assertEqual(stale_candidates(snapshot, cfg, self.NOW), {})


class R2Test(unittest.TestCase):
    def test_below_cap_allowed(self) -> None:
        blocked, reason = apply_r2(2, 3)
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_at_cap_blocked(self) -> None:
        blocked, reason = apply_r2(3, 3)
        self.assertTrue(blocked)
        self.assertIn("r2:", reason)
        self.assertIn("3", reason)

    def test_zero_cap_blocks_always(self) -> None:
        blocked, _ = apply_r2(0, 0)
        self.assertTrue(blocked)


class R3Test(unittest.TestCase):
    def test_missing_quote_fails_closed(self) -> None:
        self.assertFalse(r3_deviation_ok(10000.0, None, 0.03))
        self.assertFalse(r3_deviation_ok(10000.0, 0, 0.03))
        self.assertFalse(r3_deviation_ok(None, 10000.0, 0.03))
        self.assertFalse(r3_deviation_ok(10000.0, -5, 0.03))

    def test_within_band_ok(self) -> None:
        self.assertTrue(r3_deviation_ok(10000.0, 10000.0, 0.03))
        self.assertTrue(r3_deviation_ok(10000.0, 10300.0, 0.03))  # exactly 3%
        self.assertTrue(r3_deviation_ok(10000.0, 9700.0, 0.03))

    def test_moved_too_far_rejected(self) -> None:
        self.assertFalse(r3_deviation_ok(10000.0, 10301.0, 0.03))
        self.assertFalse(r3_deviation_ok(10000.0, 5000.0, 0.03))

    def test_reason_strings(self) -> None:
        self.assertEqual(r3_reason(10000.0, None, 0.03), "r3: no quote")
        self.assertIn("r3: price moved", r3_reason(10000.0, 11000.0, 0.03))


class R4Test(unittest.TestCase):
    def test_no_baseline_never_triggers(self) -> None:
        self.assertFalse(r4_triggered(None, 100.0, 0.03))
        self.assertFalse(r4_triggered(0.0, 100.0, 0.03))
        self.assertFalse(r4_triggered(-1.0, 100.0, 0.03))
        self.assertFalse(r4_triggered(100.0, None, 0.03))

    def test_within_limit_ok(self) -> None:
        self.assertFalse(r4_triggered(10_000_000.0, 9_700_000.0, 0.03))
        self.assertFalse(r4_triggered(10_000_000.0, 10_000_000.0, 0.03))

    def test_breach_triggers(self) -> None:
        self.assertTrue(r4_triggered(10_000_000.0, 9_699_999.0, 0.03))
        self.assertTrue(r4_triggered(10_000_000.0, 5_000_000.0, 0.03))

    def test_gain_never_triggers(self) -> None:
        self.assertFalse(r4_triggered(10_000_000.0, 11_000_000.0, 0.03))


class NewsGateParseTest(unittest.TestCase):
    """R5 news-gate payload parsing (BC-250 /newsgate)."""

    def test_block_and_ok_mixed(self) -> None:
        payload = {
            "gate": {
                "005930": {"verdict": "block", "reasons": ["부정 뉴스", "소송"]},
                "000660": {"verdict": "ok", "reasons": []},
                "111111": {"verdict": "unknown"},
            }
        }
        blocked = gate_blocked_codes(payload)
        self.assertEqual(list(blocked.keys()), ["005930"])
        self.assertIn("부정 뉴스", blocked["005930"])

    def test_no_verdict_or_missing_gate_returns_empty(self) -> None:
        self.assertEqual(gate_blocked_codes({}), {})
        self.assertEqual(gate_blocked_codes({"gate": {}}), {})
        self.assertEqual(
            gate_blocked_codes({"gate": {"005930": {"reasons": ["x"]}}}), {}
        )
        self.assertEqual(gate_blocked_codes(None), {})
        self.assertEqual(gate_blocked_codes("not json"), {})


if __name__ == "__main__":
    unittest.main()


class R1RankPercentileScaleTest(unittest.TestCase):
    """score_kind=rank_pct 이면 백분위 문턱(0~100)으로 판단한다 (계획 ③)."""

    def _snapshot(self, scores):
        items = [
            tc_support.make_candidate(
                rank=i + 1, code="00000{0}".format(i + 1),
                score="{0}".format(score), score_kind="rank_pct",
                signal_date="2026-09-29",
            )
            for i, score in enumerate(scores)
        ]
        return FeedSnapshot.from_payload(tc_support.make_payload(swing=items, close=None))

    def test_rank_threshold_blocks_low_percentile_list(self) -> None:
        cfg = RunnerConfig(r1_min_avg_rank_pct={"swing": 90.0})
        blocked = apply_r1(self._snapshot([88.0, 80.0]), cfg)  # avg 84 < 90

        self.assertIn("avg rank_pct 84.0 < 90.0", blocked["swing"])
        self.assertIn("percentile", blocked["swing"])

    def test_rank_threshold_passes_high_percentile_list(self) -> None:
        cfg = RunnerConfig(r1_min_avg_rank_pct={"swing": 90.0})

        self.assertEqual(apply_r1(self._snapshot([96.0, 92.0]), cfg), {})

    def test_rank_pct_feed_without_rank_threshold_uses_score_threshold(self) -> None:
        """문턱을 따로 주지 않으면 기존 0~100 점수 문턱으로 판단한다(둘 다 0~100)."""
        cfg = RunnerConfig(r1_min_avg_rank_pct={})
        blocked = apply_r1(self._snapshot([50.0, 40.0]), cfg)  # avg 45 < 75

        self.assertIn("avg score 45.0 < 75.0", blocked["swing"])

    def test_probability_feed_ignores_rank_threshold(self) -> None:
        """확률 스케일 피드에는 백분위 문턱을 적용하지 않는다(스케일 혼동 방지)."""
        cfg = RunnerConfig(r1_min_avg_rank_pct={"swing": 99.0})
        items = [tc_support.make_candidate(
            rank=1, code="000001", score="52.0", score_kind="calibrated_prob",
            signal_date="2026-09-29")]
        snapshot = FeedSnapshot.from_payload(
            tc_support.make_payload(swing=items, close=None))

        blocked = apply_r1(snapshot, cfg)

        # 52.0 은 백분위 문턱(99.0)으로는 걸리지 않지만 확률 문턱(0.58)으로는 걸린다
        # → 확률 스케일 피드에 백분위 문턱이 새어 들어가지 않았다는 증거.
        self.assertIn("avg prob 0.520 < 0.58", blocked["swing"])
