"""엣지 게이트 + ATR 비대칭 청산 테스트 — docs/spec_trader_edge_gate.md (신규 6개).

WHY (실측 — 스펙 선행 실측 인용, 재측정 금지): 실계좌 11건 승률 22.2% ·
평균이익 1,028원 / 평균손실 5,075원(= 손익비 0.203, **역방향**) · 기대값
−2,943원/건.  손익분기 승률(왕복 0.21%): ±1% 60.5% · ±1.5% 57.0% · R:R 3 =
43.8%.  현 신호 무손절 기준선 승률 36.0% → 진입 필터(엣지 점수)와 비대칭
R:R(ATR 1.5/3.0)을 함께 걸어 역방향을 뒤집는 것이 스펙의 설계다.  이
테스트는 ① 엣지 점수 하한 차단(수치가 사유에 남음) ② 통과 ③ 점수 결측의
fail-open/fail-closed ④ 손익비(rr) 하한 ⑤ ATR 라인 산출·상한 클램프
⑥ atr_pct 결측 시 고정 라인(1.5/2.5) 폴백을 잠근다.

구현 참고: 기존 test_engine.py::test_all_gates_pass_produces_plan 이 통과
결정의 게이트 목록을 6개로 고정하므로, 엣지 게이트 2개는 **차단일 때만**
GateResult 를 남긴다(post_exit_cooldown 과 같은 규약 — 기존 테스트 수정 금지).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from typing import Optional

from trader_core import Config, Journal
from trader_core.config import KST
from trader_core.engine import AccountState, TraderEngine
from trader_core.feed import FeedSnapshot
from trader_core.tests import support


def _now() -> datetime:
    return datetime(2026, 9, 4, 9, 30, tzinfo=KST)


class EdgeGateTest(unittest.TestCase):
    """①~⑥ 엔진 통합: 엣지 게이트·rr 게이트·ATR 청산 라인이 실제 사이징 경로에 적용된다."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="edge-gate-")
        self.journal = Journal(os.path.join(self._tmp, "j.sqlite3"))

    def tearDown(self) -> None:
        import shutil

        self.journal.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _config(
        self,
        min_edge_score: int = 0,
        edge_gate_fail_open: bool = True,
        min_rr: float = 0.0,
        screener_profiles: Optional[dict] = None,
    ) -> Config:
        cfg = Config(
            market_hours_only=False,
            daytrading_requires_market_open=False,
            kill_switch_path=os.path.join(self._tmp, "kill.txt"),
            min_edge_score=min_edge_score,
            edge_gate_fail_open=edge_gate_fail_open,
            min_rr=min_rr,
            # 동일위험 캡이 고정 stop_pct 를 쓰는 Kelly 경로를 검증한다
            # (2026-11-09 기본값 equal_weight 가 아니라 그 경로를 고정).
            sizing_mode="kelly",
        )
        if screener_profiles is not None:
            # trader_core.Config 에는 screener_profiles 필드가 없으므로
            # (Config._coerce 가 미지 키를 버림 — 2026-11-17 확인) 동적 속성으로
            # 주입한다.  엔진은 getattr(self.config, "screener_profiles", None) 로 읽는다.
            cfg.screener_profiles = screener_profiles  # type: ignore[attr-defined]
        return cfg

    def _evaluate(self, cfg: Config, candidate: dict) -> "TraderEngine.decisions":  # type: ignore[name-defined]
        payload = support.make_payload(
            close=[], swing=[], daytrading=[candidate]
        )
        snapshot = FeedSnapshot.from_payload(payload)
        engine = TraderEngine(
            bridge=None, feed_client=None, journal=self.journal, config=cfg
        )
        report = engine.evaluate(
            snapshot,
            now=_now(),
            account=AccountState(cash=10_000_000, equity=10_000_000),
            daily_count=0,
        )
        decision = next(d for d in report.decisions if d.screener == "daytrading")
        return decision

    # ① edge_score=3 < min_edge_score=5 → 차단, 사유에 수치
    def test_edge_score_below_minimum_blocks_with_numbers_in_reason(self) -> None:
        cfg = self._config(min_edge_score=5)
        decision = self._evaluate(
            cfg, support.make_candidate(code="047080", edge_score="3")
        )
        self.assertFalse(decision.passed)
        reason = decision.blocked_reason()
        assert reason is not None
        self.assertIn("edge_score", reason)
        edge_gate = next(g for g in decision.gates if g.gate == "edge_score")
        self.assertFalse(edge_gate.ok)
        self.assertIn("3", edge_gate.detail)
        self.assertIn("5", edge_gate.detail)

    # ② edge_score=6 → 통과 (게이트 기록은 "pass")
    def test_edge_score_above_minimum_passes(self) -> None:
        cfg = self._config(min_edge_score=5)
        decision = self._evaluate(
            cfg, support.make_candidate(code="047080", edge_score="6")
        )
        self.assertTrue(decision.passed, decision.blocked_reason())
        plan = decision.plan
        assert plan is not None
        self.assertEqual(plan.algo["edge_gate"], "pass")
        self.assertEqual(plan.algo["candidate"]["edge_score"], 6.0)

    # ③ edge_score=None + fail_open=True → 통과(fail-open 기록), False → 차단
    def test_missing_edge_score_fail_open_passes_and_fail_closed_blocks(self) -> None:
        open_cfg = self._config(min_edge_score=5, edge_gate_fail_open=True)
        open_decision = self._evaluate(
            open_cfg, support.make_candidate(code="047080")  # edge_score 키 없음
        )
        self.assertTrue(open_decision.passed, open_decision.blocked_reason())
        open_plan = open_decision.plan
        assert open_plan is not None
        self.assertEqual(open_plan.algo["edge_gate"], "fail_open")

        closed_cfg = self._config(min_edge_score=5, edge_gate_fail_open=False)
        closed_decision = self._evaluate(
            closed_cfg, support.make_candidate(code="047080")  # edge_score 키 없음
        )
        self.assertFalse(closed_decision.passed)
        reason = closed_decision.blocked_reason()
        assert reason is not None
        self.assertIn("edge_score", reason)
        edge_gate = next(g for g in closed_decision.gates if g.gate == "edge_score")
        self.assertFalse(edge_gate.ok)
        self.assertIn("edge_score_missing", edge_gate.detail)

    # ④ min_rr=2.0: stop 1.5%/tp 2.5%(rr=1.67) → 차단 / stop 1.5%/tp 4.5%(rr=3) → 통과
    def test_min_rr_blocks_low_reward_risk_ratio(self) -> None:
        low_rr_cfg = self._config(
            min_rr=2.0,
            screener_profiles={
                "daytrading": {
                    "stop_mode": "fixed", "stop_pct": 0.015, "tp_pct": 0.025,
                    "max_stop_pct": 0.08,
                }
            },
        )
        blocked = self._evaluate(
            low_rr_cfg, support.make_candidate(code="047080")
        )
        self.assertFalse(blocked.passed)
        reason = blocked.blocked_reason()
        assert reason is not None
        self.assertIn("rr_min", reason)
        rr_gate = next(g for g in blocked.gates if g.gate == "rr_min")
        self.assertFalse(rr_gate.ok)
        self.assertIn("1.67", rr_gate.detail)
        self.assertIn("2.00", rr_gate.detail)

        high_rr_cfg = self._config(
            min_rr=2.0,
            screener_profiles={
                "daytrading": {
                    "stop_mode": "fixed", "stop_pct": 0.015, "tp_pct": 0.045,
                    "max_stop_pct": 0.08,
                }
            },
        )
        passed = self._evaluate(
            high_rr_cfg, support.make_candidate(code="047080")
        )
        self.assertTrue(passed.passed, passed.blocked_reason())
        plan = passed.plan
        assert plan is not None
        self.assertAlmostEqual(plan.stop_pct, 0.015, places=6)
        self.assertAlmostEqual(plan.tp_pct, 0.045, places=6)

    # ⑤ stop_mode="atr": atr_pct=0.02·mult 1.5/3.0 → stop 0.03·tp 0.06,
    #    max_stop_pct 클램프 동작(atr 0.06 → stop 0.09 → 0.08 로 자름)
    def test_atr_stop_mode_computes_asymmetric_lines_with_clamp(self) -> None:
        cfg = self._config(
            screener_profiles={
                "daytrading": {
                    "stop_mode": "atr",
                    "atr_stop_mult": 1.5,
                    "atr_tp_mult": 3.0,
                    "max_stop_pct": 0.08,
                }
            }
        )
        plan_decision = self._evaluate(
            cfg, support.make_candidate(code="047080", atr_pct="0.02")
        )
        self.assertTrue(plan_decision.passed, plan_decision.blocked_reason())
        plan = plan_decision.plan
        assert plan is not None
        self.assertAlmostEqual(plan.stop_pct, 0.03, places=6)   # 0.02 x 1.5
        self.assertAlmostEqual(plan.tp_pct, 0.06, places=6)     # 0.02 x 3.0
        self.assertEqual(plan.algo["stop"]["model"], "atr")

        clamp_decision = self._evaluate(
            cfg, support.make_candidate(code="047080", atr_pct="0.06")
        )
        self.assertTrue(clamp_decision.passed, clamp_decision.blocked_reason())
        clamp_plan = clamp_decision.plan
        assert clamp_plan is not None
        # 0.06 x 1.5 = 0.09 → max_stop_pct 0.08 로 클램프
        self.assertAlmostEqual(clamp_plan.stop_pct, 0.08, places=6)
        # 0.06 x 3.0 = 0.18 (tp 상한 0.20 안)
        self.assertAlmostEqual(clamp_plan.tp_pct, 0.18, places=6)

    # ⑥ atr_pct 결측 → fixed(1.5/2.5) 폴백, algo_json.stop["model"]=="fixed"
    def test_missing_atr_falls_back_to_fixed_lines(self) -> None:
        # 기본 프로파일(profiles.py DEFAULT_PROFILES['daytrading'] =
        # stop_mode "atr" + 폴백 고정 라인 1.5/2.5) — atr_pct 가 없으면 폴백.
        cfg = self._config()
        decision = self._evaluate(
            cfg, support.make_candidate(code="047080", day_range_pct="24.0")
        )
        self.assertTrue(decision.passed, decision.blocked_reason())
        plan = decision.plan
        assert plan is not None
        self.assertAlmostEqual(plan.stop_pct, 0.015, places=6)
        self.assertAlmostEqual(plan.tp_pct, 0.025, places=6)
        self.assertEqual(plan.algo["stop"]["model"], "fixed")


if __name__ == "__main__":
    unittest.main()
