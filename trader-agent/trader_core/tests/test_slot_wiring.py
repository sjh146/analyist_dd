"""경로별 슬롯 게이트의 **엔진 배선** 테스트.

WHY 이 파일이 따로 필요한가: slot_rules.py(순수 함수)의 규칙 자체는
test_slot_rules.py 가 고정하지만, "엔진이 **저널의 경로별 미청산 행**으로 센다"는
배선 계약은 엔진 테스트로만 잡힌다. 브로커 포지션에는 스크리너 귀속이 없어서
(실측 2026-10-07 15:03 스냅샷: 미청산 3행 전부 swing, daytrading 사상 0건 —
전역 cap 3 이 swing 3 으로 포화) 저널이 유일한 귀속 출처다.

고정하는 계약(스펙 spec_slot_wiring.md 핵심):
  ① _slot_allows 는 TraderEngine 클래스의 메서드다(모듈 함수/별도 클래스 아님)
  ② 경로별 보유수 = journal.open_trades(screener=s) 의 종목 수
  ③ 셀 수 없으면(저널 없음/조회 실패) False = fail-closed
  ④ 전역 max_open_positions = 5, 총 노출 max_invested_pct = 0.30 불변
"""
from __future__ import annotations

import inspect
import os
import tempfile
from datetime import datetime, timedelta, timezone

from trader_core import AccountState, Config, FeedSnapshot, Journal, MockBridge
from trader_core.engine import TraderEngine

from trader_core.tests import support

KST = timezone(timedelta(hours=9))
FRIDAY_OPEN = datetime(2026, 9, 4, 10, 0, tzinfo=KST)


class _BrokenJournal:
    """open_trades 가 예외를 던지는 저널 대역 (fail-closed 검증용)."""

    def open_trades(self, screener=None):
        raise RuntimeError("db locked")


def _journal_with_open(*rows):
    """rows = [(screener, [codes, ...]), ...] — 미청산 매수 행을 저널에 기록."""
    journal = Journal(":memory:")
    for screener, codes in rows:
        for code in codes:
            journal.record_trade(
                side="buy", code=code, qty=1, price=10000.0,
                order_type="limit", screener=screener,
            )
    return journal


def _engine(journal=None, config=None) -> TraderEngine:
    return TraderEngine(
        MockBridge(start_cash=10_000_000),
        feed_client=None,
        config=config
        if config is not None
        else Config(
            market_hours_only=False,
            daytrading_requires_market_open=False,
        ),
        journal=journal,
    )


def _cfg(kill_dir: str) -> Config:
    return Config(
        market_hours_only=False,
        daytrading_requires_market_open=False,
        kill_switch_path=os.path.join(kill_dir, "kill.txt"),
    )


# --------------------------------------------------------------------------- #
# _slot_allows 배선 계약
# --------------------------------------------------------------------------- #
def test_slot_allows_is_defined_on_the_engine_class():
    """핵심 ①: _slot_allows 는 Engine 클래스 안의 메서드다."""
    assert "_slot_allows" in TraderEngine.__dict__
    assert inspect.isfunction(TraderEngine.__dict__["_slot_allows"])


def test_slot_allows_counts_journal_open_trades_per_screener():
    """핵심 ②: 경로별 보유수 = journal.open_trades(screener=) — 스윙 3 은 스윙만 막는다."""
    journal = _journal_with_open(("swing", ["A000001", "A000002", "A000003"]))
    engine = _engine(journal=journal)
    assert engine._slot_allows("swing") is False      # 3/3 포화
    assert engine._slot_allows("daytrading") is True  # 0/2 — 실측상황의 반대 방향 검증


def test_planned_same_screener_counts_against_slot():
    journal = _journal_with_open(("daytrading", ["A000001"]))
    engine = _engine(journal=journal)
    assert engine._slot_allows("daytrading") is True
    assert engine._slot_allows("daytrading", planned_same_screener=1) is False


def test_close_path_defers_to_global_cap():
    """슬롯 미정의 경로(close)는 GLOBAL_FALLBACK_SCREENERS → 항상 허용,
    전역 cap 게이트가 대신 막는다(현행 유지)."""
    journal = _journal_with_open(("close", ["A000001"] * 10))
    engine = _engine(journal=journal)
    assert engine._slot_allows("close") is True


def test_missing_journal_fails_closed():
    """핵심 ③: 저널이 없으면 셀 수 없으므로 False."""
    engine = _engine(journal=None)
    assert engine._slot_allows("swing") is False
    assert engine._slot_allows("daytrading") is False


def test_journal_query_failure_fails_closed():
    """핵심 ③: 조회가 예외를 던져도 False(진입 차단)."""
    engine = _engine(journal=_BrokenJournal())
    assert engine._slot_allows("swing") is False


def test_slot_overrides_are_honored():
    journal = _journal_with_open(("daytrading", ["A000001"]))
    cfg = Config(max_open_positions_by_screener={"daytrading": 1})
    engine = _engine(journal=journal, config=cfg)
    assert engine._slot_allows("daytrading") is False


# --------------------------------------------------------------------------- #
# evaluate() 배선 — 게이트 파이프라인 안에서 실제로 발동하는지
# --------------------------------------------------------------------------- #
def test_evaluate_blocks_swing_at_slot_but_allows_daytrading():
    """실측 상황 재현: 저널에 swing 3 이 열려 있으면 swing 후보는 슬롯에서
    막히고, daytrading 후보는 슬롯 0/2 로 통과한다(전역 cap 5 안에서)."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = _journal_with_open(("swing", ["A000001", "A000002", "A000003"]))
        engine = _engine(journal=journal, config=_cfg(tmp))
        snapshot = FeedSnapshot.from_payload(support.make_payload(
            swing=[support.make_candidate(code="005930")],
            daytrading=[support.make_candidate(code="047080")],
        ))
        report = engine.evaluate(
            snapshot, now=FRIDAY_OPEN,
            account=AccountState(cash=10_000_000, equity=10_000_000),
            daily_count=0,
        )
    by_screener = {d.screener: d for d in report.decisions}
    assert not by_screener["swing"].passed
    assert "슬롯" in by_screener["swing"].blocked_reason()
    assert by_screener["daytrading"].passed


def test_global_cap_still_blocks_when_broker_holds_five():
    """핵심 ④: 전역 게이트는 브로커 실측(5종목)으로 여전히 막는다 — 슬롯이 비어
    있어도 전역 동시보유 5 가 최종 방어선이다."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = _journal_with_open(("swing", ["A000001", "A000002", "A000003"]))
        engine = _engine(journal=journal, config=_cfg(tmp))
        positions = [
            {"code": "B{0:06d}".format(i), "qty": 10, "avg_price": 1000.0}
            for i in range(1, 6)
        ]
        account = AccountState(
            cash=10_000_000, equity=10_000_000, positions=positions
        )
        snapshot = FeedSnapshot.from_payload(support.make_payload(
            daytrading=[support.make_candidate(code="047080")],
        ))
        report = engine.evaluate(
            snapshot, now=FRIDAY_OPEN, account=account, daily_count=0
        )
    decision = report.decisions[0]
    assert not decision.passed
    assert "max_open_positions" in decision.blocked_reason()


def test_config_global_cap_three_and_full_deposit_deployment():
    """2026-11-09 사용자 지시: 최대 3종목, 예수금 100% 노출."""
    cfg = Config()
    assert cfg.max_open_positions == 3
    assert cfg.max_invested_pct == 1.0
    assert cfg.max_open_positions_by_screener == {"swing": 2, "daytrading": 2}
    assert cfg.sizing_mode == "equal_weight"
    assert abs(cfg.max_position_pct - 1.0 / 3.0) < 1e-4
