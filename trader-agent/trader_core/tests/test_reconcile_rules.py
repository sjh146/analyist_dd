"""계류 주문 정정 금지 규칙 테스트 (2026-10-06 실사고 고정).

실사고: 브로커 filled_qty=0 만 보고 '미체결 → 원가 청산'으로 정정했는데, 그 주문은
cancelable_qty=1 인 **살아있는(계류) 주문**이었고 정정 뒤 체결됐다 → 저널/브로커가 반대로 어긋남.
"""
from trader_core.reconcile_rules import may_correct_as_unfilled, needs_recheck, order_state


def test_filled():
    assert order_state(2, 0) == "filled"
    assert not may_correct_as_unfilled("filled")


def test_resting_order_must_not_be_corrected():
    # 계류: 체결 0 + 취소가능 1 (319400 실사고 그대로)
    assert order_state(0, 1) == "resting"
    assert may_correct_as_unfilled("resting") is False
    assert needs_recheck("resting") is True


def test_rejected_order_can_be_corrected():
    # 거부: 체결 0 + 취소가능 0 (접수 실패)
    assert order_state(0, 0) == "rejected"
    assert may_correct_as_unfilled("rejected") is True


def test_unknown_when_no_values():
    assert order_state(None, 1) == "unknown"
    assert may_correct_as_unfilled("unknown") is False
    assert needs_recheck("unknown") is True


def test_zero_cancelable_is_decisive_only_with_a_real_order_no():
    """주문번호가 없으면(접수 실패) 거부로 단정하지 않는다 — 추측 금지."""
    assert order_state(0, None, order_no=8629) == "unknown"
    assert order_state(0, None, order_no=0) == "resting"
