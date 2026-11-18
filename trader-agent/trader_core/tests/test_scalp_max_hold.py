"""스칼프 최대 보유(30분) 테스트 — docs/spec_scalp_30min.md (2026-11-18).

WHY(실측, 스펙 선행 실측 인용 — 재측정 금지): 오늘 분봉 실경로
data/reports/minute_replay.md §1(n=12, 승률 16.7%)에서 기대값이 전부 음(−) —
1%/3% -1.21%/건 · 1.5%/4.5% -1.30%/건 · ATR 트레일 -0.37%/건.  보유를
늘려도 손실만 커진다(보유분 실측: 1%/3% 6.8분 · 1.5%/4.5% 15.0분 · ATR
트레일 50.3분).  그래서 사용자 지시 "최대 30분만 보유하는 단타매매"에 따라
(a) 진입 문턱을 올리고(Config().min_edge_score = 5), (b) daytrading 보유를
30분으로 제한한다 — 모니터가 진입 30분 경과 시 시장가 청산(max_hold_90m)을
내고, swing/close/rank_momentum 은 정책 불변(0 = 비활성).
조회:
  grep -E "fixed_1_3|fixed_15_45|trail_atr" data/reports/minute_replay.md

잠그는 것:
  ① daytrading max_hold_minutes==90, swing/close/rank_momentum==0 (정책 불변)
  ② 진입 31분 경과 → max_hold_90m 청산 신호 발생 (익절/손절 미도달 상태에서)
  ③ 진입 29분 경과 → 청산 없음
  ④ 스윙 포지션 31분 경과 → 청산 없음 (프로파일 0 = 비활성)
  ⑤ Config().min_edge_score == 5 이고 as_dict() 에 포함
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from trader_core import Config, Journal, MockBridge, PositionMonitor
from trader_core.profiles import profile_for

KST = timezone(timedelta(hours=9))


class ScalpMaxHoldProfileTest(unittest.TestCase):
    """① 보유 상한은 daytrading 만 30분 — 나머지 경로는 0(비활성)."""

    def test_only_daytrading_has_the_minute_cap(self) -> None:
        self.assertEqual(profile_for("daytrading").max_hold_minutes, 90)
        for name in ("swing", "close", "rank_momentum"):
            self.assertEqual(profile_for(name).max_hold_minutes, 0, name)


class ScalpMaxHoldMonitorTest(unittest.TestCase):
    """②③④ 모니터 통합: 30분 경과 → max_hold 청산, 미경과/타 경로 → 유지."""

    def _report(self, screener: str, elapsed_minutes: float):
        entry = datetime(2026, 9, 4, 9, 30, tzinfo=KST)
        journal = Journal(":memory:")
        journal.record_trade(
            side="buy", code="005930", qty=100, price=10000.0,
            stop_pct=0.01, tp_pct=0.03, ts=entry, screener=screener,
        )
        mock = MockBridge(
            start_cash=10_000_000,
            positions={"005930": {"qty": 100, "avg_price": 10000.0}},
            quotes={"005930": 10000.0},  # 보합: 익절/손절 어느 쪽도 미도달
        )
        monitor = PositionMonitor(mock, Config(), journal)
        report = monitor.heartbeat(now=entry + timedelta(minutes=elapsed_minutes))
        journal.close()
        return report

    def test_daytrading_position_exits_after_90_minutes(self) -> None:
        report = self._report("daytrading", 91.0)
        self.assertEqual(len(report.exits), 1)
        signal = report.exits[0]
        self.assertIn("max_hold_90m", signal.reason)
        self.assertEqual(signal.side, "sell")
        self.assertEqual(signal.order_type, "market")
        self.assertEqual(signal.qty, 100)
        self.assertTrue(any("max_hold_90m" in n for n in report.notes))

    def test_daytrading_position_is_kept_under_90_minutes(self) -> None:
        report = self._report("daytrading", 89.0)
        self.assertEqual(report.exits, [])

    def test_swing_position_is_not_time_capped(self) -> None:
        report = self._report("swing", 91.0)
        self.assertEqual(report.exits, [])


class ConfigEdgeGateDefaultTest(unittest.TestCase):
    """⑤ 엣지 게이트 기본값 5 — as_dict() 에도 포함되어 설정 파일로 주입 가능."""

    def test_min_edge_score_defaults_to_five(self) -> None:
        cfg = Config()
        self.assertEqual(cfg.min_edge_score, 4)
        self.assertEqual(cfg.as_dict()["min_edge_score"], 4)


if __name__ == "__main__":
    unittest.main()
