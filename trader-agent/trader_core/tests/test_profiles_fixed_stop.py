"""WP2 고정 라인(1.5%/2.5%) 프로파일 테스트 — docs/spec_daytrading_fixed_lines.md.

WHY (실측, 상위 지시서 docs/spec_daytrading_shortstop_2026-11-17.md 0-3/0-4 인용 —
재측정 금지): 현재 라이브는 스윙이다(전역 stop_loss_pct 0.07 / take_profit_pct 0.07,
2026-11-17 실행 확인).  ScreenerProfile 에 stop_pct/tp_pct 필드가 아예 없어서
daytrading 프로파일도 전역 7%/7% 를 썼다.  그리드 11,300건(2026-04-01~11-14,
scripts/_stoploss_grid.py)에서 7%/7% 중립 -0.822% → 1%/2% -0.151%(측정된 최고).
왕복비용 0.21%(실체결)라 ±1.5% 라인의 손익분기 승률은 57.0% — 이 테스트는
① daytrading 이 고정 라인(손절 1.5% / 익절 2.5%, 상한 2%)을 쓰고,
② swing/close/rank_momentum 은 기존 변동성 경로("vol", None/None)를 보존하며,
③ max_stop_pct 상한이 엔진에서 실제로 적용되고,
④⑤ 엔진 통합에서 변동성이 커도 고정 라인이 덮이지 않는 것과 swing 이 여전히
변동성 경로를 쓰는 것을 잠근다.  롤백은 stop_mode="vol" 한 줄이다.
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
from trader_core.profiles import profile_for
from trader_core.tests import support


def _now() -> datetime:
    return datetime(2026, 9, 4, 9, 30, tzinfo=KST)


class FixedLineProfileTest(unittest.TestCase):
    """① daytrading 기본값 = 고정 라인 / ② 다른 프로파일 = 기존 "vol" 보존."""

    def test_daytrading_profile_uses_the_fixed_lines(self) -> None:
        profile = profile_for("daytrading")
        self.assertEqual(profile.stop_mode, "fixed")
        self.assertEqual(profile.stop_pct, 0.015)
        self.assertEqual(profile.tp_pct, 0.025)
        self.assertEqual(profile.max_stop_pct, 0.02)
        self.assertIs(profile.hold_overnight, False)
        self.assertEqual(profile.entry_window, "09:05-14:30")
        self.assertEqual(profile.close_exit_window, "15:00-15:20")

    def test_other_profiles_keep_the_volatility_path(self) -> None:
        for name in ("swing", "close", "rank_momentum"):
            profile = profile_for(name)
            self.assertEqual(profile.stop_mode, "vol", name)
            self.assertIsNone(profile.stop_pct, name)
            self.assertIsNone(profile.tp_pct, name)


class FixedStopEngineTest(unittest.TestCase):
    """③④⑤ 엔진 통합: 고정 라인·상한이 실제 사이징 경로에 적용된다."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="fixed-stop-")
        self.journal = Journal(os.path.join(self._tmp, "j.sqlite3"))

    def tearDown(self) -> None:
        import shutil

        self.journal.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _config(self, screener_profiles: Optional[dict] = None) -> Config:
        cfg = Config(
            market_hours_only=False,
            daytrading_requires_market_open=False,
            kill_switch_path=os.path.join(self._tmp, "kill.txt"),
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

    def _evaluate(
        self, cfg: Config, payload: dict, screener: str
    ):
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
        decision = next(d for d in report.decisions if d.screener == screener)
        self.assertTrue(decision.passed, decision.blocked_reason())
        assert decision.plan is not None
        return decision.plan

    def test_max_stop_pct_caps_the_fixed_line(self) -> None:
        """③ stop_pct=0.05, max_stop_pct=0.02 → 엔진이 0.02 로 자른다."""
        cfg = self._config(
            {
                "daytrading": {
                    "stop_mode": "fixed",
                    "stop_pct": 0.05,
                    "tp_pct": 0.025,
                    "max_stop_pct": 0.02,
                }
            }
        )
        payload = support.make_payload(
            close=[],
            swing=[],
            daytrading=[support.make_candidate(code="047080")],
        )
        plan = self._evaluate(cfg, payload, "daytrading")
        self.assertAlmostEqual(plan.stop_pct, 0.02, places=6)
        self.assertAlmostEqual(plan.tp_pct, 0.025, places=6)
        self.assertEqual(plan.algo["stop"]["model"], "fixed")

    def test_daytrading_decision_uses_the_fixed_lines_even_when_volatile(self) -> None:
        """④ 변동성이 커도(24% day range → 변동성 스톱이면 12%) 고정 1.5%/2.5% 를 쓴다."""
        cfg = self._config()
        payload = support.make_payload(
            close=[],
            swing=[],
            daytrading=[
                support.make_candidate(code="047080", day_range_pct="24.0")
            ],
        )
        plan = self._evaluate(cfg, payload, "daytrading")
        self.assertAlmostEqual(plan.stop_pct, 0.015, places=6)
        self.assertAlmostEqual(plan.tp_pct, 0.025, places=6)
        self.assertEqual(plan.algo["stop"]["model"], "fixed")

    def test_swing_decision_keeps_the_volatility_path(self) -> None:
        """⑤ swing 은 변동성 경로(0.07 이상)를 그대로 쓴다 — 기존 동작 보존."""
        cfg = self._config()
        payload = support.make_payload(
            close=[],
            daytrading=[],
            swing=[support.make_candidate(code="005930", day_range_pct="24.0")],
        )
        plan = self._evaluate(cfg, payload, "swing")
        # volatility_stop_pct(24, floor=0.07, factor=0.5, cap=0.20) = 0.12
        self.assertGreaterEqual(plan.stop_pct, 0.07)
        self.assertAlmostEqual(plan.stop_pct, 0.12, places=6)
        # 변동성 경로 라벨: "vol-range" (기존 기대값 — 스펙의 "volatility" 표기는
        # test_algo_attribution.py:195 가 고정한 기존 라벨과 충돌해 유지하지 않는다).
        self.assertEqual(plan.algo["stop"]["model"], "vol-range")


if __name__ == "__main__":
    unittest.main()
