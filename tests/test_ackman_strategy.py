"""AckmanStrategy mock unit tests (테제원장 M4 — DB/외부 API 0건).

duck-typed MockStorage를 주입해 전략 로직(4종 매도 판정·진입/사이징·point-in-time asof)을
순수하게 검증한다. psycopg2/redis는 import하지 않는다 (전략 모듈은 `base_strategy`만 의존).
"""

import inspect
import os
import sys
from datetime import date, timedelta

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "strategy-agents"))

from app.strategies.ackman_strategy import (  # noqa: E402
    AckmanStrategy,
    STRATEGY_NAME,
    position_quantity,
)
import app.strategies.ackman_strategy as ack_module  # noqa: E402

ASOF = date(2024, 8, 27)


# ── MockStorage (duck-typed, test_quality_strategy.py:31-67 관례) ──────────


class MockStorage:
    def __init__(self, theses=None, verdicts=None, positions=None, prices=None, config=None):
        self.theses = theses or []
        self.verdicts = verdicts or {}   # {thesis_id: [{"verdict", "verdict_date"}, ...]} DESC
        self.positions = positions or []  # [{"stock_code", "quantity"}]
        self.prices = prices or {}        # {stock_code: float}
        self.config = config or {}

    def get_strategy_config(self, name):
        return self.config

    def get_active_theses(self, strategy_name):
        return self.theses

    def get_thesis_verdicts(self, thesis_id, limit):
        return self.verdicts.get(thesis_id, [])[:limit]

    def get_positions(self):
        return self.positions

    def get_latest_price(self, stock_code):
        return self.prices.get(stock_code)


# ── 테제 픽스처 헬퍼 ────────────────────────────────────────────────────────


def _thesis(tid, code, intrinsic=None, created="2024-08-27"):
    """position_theses 행 shape (get_active_theses 출력)."""
    return {"id": tid, "stock_code": code, "thesis_text": "테제 본문",
            "disproof_criteria": "반박 증거", "intrinsic_value": intrinsic,
            "entry_price": None, "catalyst_events": None, "created_at": created}


def _verdict(v, verdict_date="2024-08-27"):
    """thesis_verdicts 행 shape (verdict_date DESC)."""
    return {"verdict": v, "verdict_date": verdict_date}


# ── 매도 판정 (_exit_reason) ────────────────────────────────────────────────


def test_exit_broken_latest():
    strategy = AckmanStrategy(MockStorage())
    verdicts = [_verdict("파기")]
    assert strategy._exit_reason(_thesis(1, "005930"), verdicts, ASOF) == "파기"


def test_exit_damage_two_consecutive_promotes():
    strategy = AckmanStrategy(MockStorage())
    thesis = _thesis(1, "005930")
    # 최근 2건 모두 손상 → 파기 승격
    assert strategy._exit_reason(thesis, [_verdict("손상"), _verdict("손상")], ASOF) == "손상_2연속_승격"
    # 손상 + 유지 → 승격 없음
    assert strategy._exit_reason(thesis, [_verdict("손상"), _verdict("유지")], ASOF) is None
    # 손상 1건만 → 승격 없음
    assert strategy._exit_reason(thesis, [_verdict("손상")], ASOF) is None


def test_exit_mos_threshold():
    intrinsic = 100_000.0
    # 최신 종가 == intrinsic × 0.95 → MoS 달성
    hit = AckmanStrategy(MockStorage(prices={"005930": intrinsic * 0.95}))
    assert hit._exit_reason(_thesis(1, "005930", intrinsic=intrinsic), [], ASOF) == "MoS_달성"
    # 0.94 × intrinsic → 미달
    miss = AckmanStrategy(MockStorage(prices={"005930": intrinsic * 0.94}))
    assert miss._exit_reason(_thesis(1, "005930", intrinsic=intrinsic), [], ASOF) is None
    # intrinsic_value None → fail-open (스킵)
    noint = AckmanStrategy(MockStorage(prices={"005930": intrinsic}))
    assert noint._exit_reason(_thesis(1, "005930"), [], ASOF) is None
    # 가격 결측(None) → 스킵
    noprice = AckmanStrategy(MockStorage(prices={}))
    assert noprice._exit_reason(_thesis(1, "005930", intrinsic=intrinsic), [], ASOF) is None


def test_exit_holding_period():
    strategy = AckmanStrategy(MockStorage())
    # created_at이 asof보다 731일 전 → 보유 2년 초과 (2022-08-27 → 2024-08-27 = 731일)
    t731 = _thesis(1, "005930", created=date(2022, 8, 27).isoformat())
    assert strategy._exit_reason(t731, [], ASOF) == "보유_2년_초과"
    # 729일 전 → 미달
    t729 = _thesis(1, "005930", created=(ASOF - timedelta(days=729)).isoformat())
    assert strategy._exit_reason(t729, [], ASOF) is None
    # created_at None → 스킵
    assert strategy._exit_reason(_thesis(1, "005930", created=None), [], ASOF) is None


def test_exit_priority_broken_over_mos():
    # 파기 + MoS 동시 충족 → 우선순위상 파기
    intrinsic = 100_000.0
    thesis = _thesis(1, "005930", intrinsic=intrinsic)
    verdicts = [_verdict("파기")]
    strategy = AckmanStrategy(MockStorage(prices={"005930": intrinsic * 0.95}))
    assert strategy._exit_reason(thesis, verdicts, ASOF) == "파기"


# ── analyze() 시그널 생성 ───────────────────────────────────────────────────


def test_analyze_buy_active_not_held():
    # 활성 테제 1건(매도 조건 없음) + 미보유 → 진입 buy 1건
    strategy = AckmanStrategy(MockStorage(theses=[_thesis(1, "005930")]))
    signals = strategy.analyze(asof_date="2024-08-27")
    assert len(signals) == 1
    sig = signals[0]
    assert sig["action"] == "buy"
    assert sig["stock_code"] == "005930"
    assert sig["strategy_name"] == STRATEGY_NAME
    assert sig["confidence"] == 0.75
    assert sig["thesis_id"] == 1
    assert sig["position_size_pct"] == 0.15
    assert sig["price"] == 0


def test_analyze_no_buy_when_held():
    # 보유 중 + 매도 조건 없음 → buy 0건·sell 0건
    strategy = AckmanStrategy(MockStorage(
        theses=[_thesis(1, "005930")],
        positions=[{"stock_code": "005930", "quantity": 10}],
    ))
    assert strategy.analyze(asof_date="2024-08-27") == []


def test_analyze_sell_when_held_and_broken():
    # 보유 + 파기 → sell 1건
    strategy = AckmanStrategy(MockStorage(
        theses=[_thesis(1, "005930")],
        verdicts={1: [_verdict("파기", "2024-08-28")]},
        positions=[{"stock_code": "005930", "quantity": 10}],
    ))
    signals = strategy.analyze(asof_date="2024-08-28")
    sells = [s for s in signals if s["action"] == "sell"]
    assert len(sells) == 1
    assert "파기" in sells[0]["reason"]
    assert sells[0]["confidence"] == 1.0


def test_analyze_exit_suppresses_buy():
    # 매도 조건 충족(파기) + 미보유 → sell 0건 + buy 0건 (빈 리스트)
    strategy = AckmanStrategy(MockStorage(
        theses=[_thesis(1, "005930")],
        verdicts={1: [_verdict("파기")]},
    ))
    assert strategy.analyze(asof_date="2024-08-27") == []


def test_position_quantity_pure():
    # 1e7 × 0.15 / 50,000 = 30
    assert position_quantity(50000, 1e7, 0.15) == 30
    assert position_quantity(None, 1e7, 0.15) == 0
    assert position_quantity(50000, 0, 0.15) == 0
    assert position_quantity(50000, -100, 0.15) == 0


def test_analyze_mixed_happy():
    # 테제 A(보유+파기→sell) + 테제 B(미보유·조건 없음→buy) → 정확히 2건 (각 1건)
    strategy = AckmanStrategy(MockStorage(
        theses=[_thesis(1, "005930"), _thesis(2, "000660")],
        verdicts={1: [_verdict("파기")]},
        positions=[{"stock_code": "005930", "quantity": 10}],
    ))
    signals = strategy.analyze(asof_date="2024-08-27")
    assert len(signals) == 2
    buys = [s for s in signals if s["action"] == "buy"]
    sells = [s for s in signals if s["action"] == "sell"]
    assert len(buys) == 1 and buys[0]["stock_code"] == "000660"
    assert len(sells) == 1 and sells[0]["stock_code"] == "005930"


def test_analyze_empty_theses():
    strategy = AckmanStrategy(MockStorage(theses=[]))
    assert strategy.analyze() == []


def test_analyze_fail_open_storage_error():
    # get_active_theses 예외 → [] (크래시 없음)
    class FailingStorage(MockStorage):
        def get_active_theses(self, strategy_name):
            raise Exception("db down")

    strategy = AckmanStrategy(FailingStorage())
    assert strategy.analyze() == []


def test_config_paper_only():
    # strategies.yaml — ackman_fundamental은 is_active + paper_only
    path = os.path.join(REPO_ROOT, "config", "strategies", "strategies.yaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    ackman = data["strategies"]["ackman_fundamental"]
    assert ackman["is_active"] is True
    assert ackman["parameters"]["paper_only"] is True


def test_backtest_smoke():
    # mock 백테스트: t0(판정 없음·미보유) → buy, t1(파기·보유) → sell.
    # 시간에 따라 상태가 바뀌는 MockStorage 2개를 주입해 진입→청산 시퀀스를 단언.
    t0 = AckmanStrategy(MockStorage(theses=[_thesis(1, "005930")]))
    assert [s["action"] for s in t0.analyze(asof_date="2024-08-27")] == ["buy"]

    t1 = AckmanStrategy(MockStorage(
        theses=[_thesis(1, "005930")],
        verdicts={1: [_verdict("파기", "2024-08-28")]},
        positions=[{"stock_code": "005930", "quantity": 10}],
    ))
    assert [s["action"] for s in t1.analyze(asof_date="2024-08-28")] == ["sell"]


def test_analyze_asof_accepts_str_and_date():
    # point-in-time 파라미터: str / date 모두 정상 동작
    strategy = AckmanStrategy(MockStorage(theses=[_thesis(1, "005930")]))
    assert [s["action"] for s in strategy.analyze(asof_date="2024-08-27")] == ["buy"]
    assert [s["action"] for s in strategy.analyze(asof_date=date(2024, 8, 27))] == ["buy"]


def test_analyze_ignores_inactive_strategy():
    # 전략별 격리: get_active_theses가 ackman_fundamental 테제를 0건 반환
    # (즉 "ackman 이외 전략 테제만 존재" = ackman은 없음) → 시그널 0건.
    class NoAckmanTheses(MockStorage):
        def get_active_theses(self, strategy_name):
            return []

    strategy = AckmanStrategy(NoAckmanTheses())
    assert strategy.analyze() == []


def test_stop_loss_contract_unchanged():
    # 가드: 전략 모듈은 신호 생성만 — 발행(publish)은 main.py 계층, 실경로 차단 구조 고정.
    assert not hasattr(AckmanStrategy, "publish_ackman_signal")
    assert not hasattr(AckmanStrategy, "publish_signal")
    src = inspect.getsource(ack_module)
    assert "import redis" not in src
    assert "import psycopg2" not in src
