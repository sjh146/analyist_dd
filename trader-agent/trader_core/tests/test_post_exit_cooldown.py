"""청산 후 재투입 쿨다운 게이트(post_exit_cooldown) 테스트.

WHY(실측): 청산 직후 같은 경로(screener)로 재진입해 두 번 손실을 본 사례 —
data/reports/reentry_after_exit.json(2026-11-17): 재투입 5쌍 합산 -33,463원.
2026-11-16 tick stop -5,331원 3.9초 뒤 74,400원 재진입,
2026-11-17 tick stop -6,831원 45.7초 뒤 72,310원 재진입
(journal/trade_journal.sqlite3 조회로 확인 — 조회 명령은 config.py 의
post_exit_cooldown_min 주석 참조).  docs/spec_post_exit_cooldown.md 승인 근거.

고정하는 계약(spec_post_exit_cooldown.md 핵심):
  ① 청산 후 cooldown(기본 30분) 안이면 post_exit_cooldown gate False 로 차단
  ② 31분 뒤면 통과(전체 게이트 그린 = plan 생성)
  ③ scope="screener"(기본) 는 같은 경로만 차단 — swing 청산이 daytrading 을 막지 않는다
  ④ post_exit_cooldown_min=0 → 비활성
  ⑤ 저널 조회 예외/저널 없음 → fail-closed 차단
  ⑥ scope="global" → 다른 screener 도 차단

등록 규약: 게이트는 **차단일 때만** GateResult 를 남긴다(통과 시 게이트 목록에
없음) — 기존 test_engine.py(test_all_gates_pass_produces_plan)가 통과 결정의
게이트 목록을 6개로 고정해 두어 그렇다(engine.py 등록부 주석 참조).

저널 대역·엔진 조립 관용구는 test_slot_wiring.py 를 그대로 따른다.  이 파일은
기존 테스트 기대값을 건드리지 않는다(신규 파일).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

from trader_core import AccountState, Config, FeedSnapshot, Journal, MockBridge
from trader_core.engine import TraderEngine

from trader_core.tests import support

KST = timezone(timedelta(hours=9))
FRIDAY_OPEN = datetime(2026, 9, 4, 10, 0, tzinfo=KST)


class _BrokenExitJournal:
    """last_exit_ts 만 예외를 던지는 저널 대역 (fail-closed 검증용).

    나머지 메서드(record_decision 등)는 실제 인메모리 저널로 위임해
    evaluate() 전 경로가 끝까지 돌 수 있게 한다.
    """

    def __init__(self) -> None:
        self._inner = Journal(":memory:")

    def last_exit_ts(self, screener=None):
        raise RuntimeError("db locked")

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _journal_with_exit(screener: str, exit_min_ago: float, code: str = "A000001"):
    """exit_ts 가 지금부터 exit_min_ago 분 전인 청산 행을 저널에 기록."""
    journal = Journal(":memory:")
    trade_id = journal.record_trade(
        side="buy", code=code, qty=1, price=10000.0,
        order_type="limit", screener=screener,
    )
    journal.close_trade(
        trade_id, exit_price=9900.0, exit_reason="tick stop",
        exit_ts=datetime.now(KST) - timedelta(minutes=exit_min_ago),
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


def _cfg(kill_dir: str, **overrides) -> Config:
    return Config(
        market_hours_only=False,
        daytrading_requires_market_open=False,
        kill_switch_path=os.path.join(kill_dir, "kill.txt"),
        **overrides,
    )


def _evaluate(engine: TraderEngine, screener: str, code: str, now=FRIDAY_OPEN):
    """screener 하나에만 후보를 넣고 evaluate 해서 그 결정 1건을 돌려준다."""
    lists = {"close": [], "swing": [], "daytrading": []}
    lists[screener] = [support.make_candidate(code=code)]
    snapshot = FeedSnapshot.from_payload(support.make_payload(**lists))
    report = engine.evaluate(
        snapshot, now=now,
        account=AccountState(cash=10_000_000, equity=10_000_000),
        daily_count=0,
    )
    return report.decisions[0]


def _cooldown_gate(decision):
    return next(g for g in decision.gates if g.gate == "post_exit_cooldown")


def _has_cooldown_gate(decision):
    return any(g.gate == "post_exit_cooldown" for g in decision.gates)


# --------------------------------------------------------------------------- #
# 게이트 판정
# --------------------------------------------------------------------------- #
def test_blocks_reentry_within_cooldown():
    """① 청산 10분 전 → 진입 차단(gate False, 사유에 '쿨다운')."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = _journal_with_exit("swing", exit_min_ago=10.0)
        engine = _engine(journal=journal, config=_cfg(tmp))
        decision = _evaluate(engine, "swing", "005930")
    gate = _cooldown_gate(decision)
    assert gate.ok is False
    assert "쿨다운" in gate.detail
    assert not decision.passed


def test_allows_reentry_after_cooldown():
    """② 청산 31분 전 → 통과(전체 게이트 그린, plan 생성, 게이트 미기록)."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = _journal_with_exit("swing", exit_min_ago=31.0)
        engine = _engine(journal=journal, config=_cfg(tmp))
        decision = _evaluate(engine, "swing", "005930")
    assert not _has_cooldown_gate(decision)  # 통과 시에는 기록하지 않는다(등록 규약)
    assert decision.passed


def test_swing_exit_does_not_block_daytrading():
    """③ scope=screener(기본): swing 청산이 daytrading 진입을 막지 않는다."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = _journal_with_exit("swing", exit_min_ago=10.0)
        engine = _engine(journal=journal, config=_cfg(tmp))
        decision = _evaluate(engine, "daytrading", "047080")
    assert not _has_cooldown_gate(decision)
    assert decision.passed


def test_zero_cooldown_disables_gate():
    """④ post_exit_cooldown_min=0 → 비활성(청산 직후여도 통과)."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = _journal_with_exit("swing", exit_min_ago=0.0)
        engine = _engine(
            journal=journal,
            config=_cfg(tmp, post_exit_cooldown_min=0.0),
        )
        decision = _evaluate(engine, "swing", "005930")
    assert not _has_cooldown_gate(decision)
    assert decision.passed


def test_journal_failure_fails_closed():
    """⑤ 저널 조회 예외 → fail-closed 차단; 저널 없음도 차단('저널 없음' 명시)."""
    with tempfile.TemporaryDirectory() as tmp:
        engine = _engine(journal=_BrokenExitJournal(), config=_cfg(tmp))
        decision = _evaluate(engine, "swing", "005930")
    gate = _cooldown_gate(decision)
    assert gate.ok is False
    assert "쿨다운" in gate.detail
    assert not decision.passed

    with tempfile.TemporaryDirectory() as tmp:
        engine = _engine(journal=None, config=_cfg(tmp))
        decision = _evaluate(engine, "swing", "005930")
    gate = _cooldown_gate(decision)
    assert gate.ok is False
    assert "저널 없음" in gate.detail


def test_global_scope_blocks_other_screeners():
    """⑥ scope='global' 이면 다른 screener(daytrading) 도 차단."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = _journal_with_exit("swing", exit_min_ago=10.0)
        engine = _engine(
            journal=journal,
            config=_cfg(tmp, post_exit_cooldown_scope="global"),
        )
        decision = _evaluate(engine, "daytrading", "047080")
    gate = _cooldown_gate(decision)
    assert gate.ok is False
    assert "쿨다운" in gate.detail
    assert not decision.passed
