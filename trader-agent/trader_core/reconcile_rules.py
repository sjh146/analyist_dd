"""정합 판정 규칙 — '미체결(계류)'과 '거부'를 구분한다.

WHY (2026-10-06 실사고): 저널에 미청산으로 남은 319400 주문을 "브로커 filled_qty=0" 만 보고
`unfilled → 원가 청산(손익 0)` 으로 정정했다. 그러나 그 주문은 **거부가 아니라 계류(resting)** 였고
(브로커 주문목록에 `cancelable_qty=1` 로 **살아 있었다**), 정정 뒤에 실제로 체결됐다.
그 결과 저널(닫힘)과 브로커(보유)가 반대로 어긋났고, 되돌리는 정합을 한 번 더 돌려야 했다.

규칙: **주문이 아직 살아 있으면 정정하지 않는다.** 정정은 취소·만료·체결로 **결말이 확인된 뒤**에만.
"""
from __future__ import annotations

from typing import Optional


def order_state(filled_qty: Optional[float], cancelable_qty: Optional[float],
                order_no: Optional[int] = None) -> str:
    """브로커 주문 1건의 상태를 판정한다.

    반환: 'filled' | 'resting' | 'rejected' | 'unknown'
      - filled   : 체결 수량 > 0  → 보유/청산 정합 대상
      - resting  : 체결 0 이지만 **취소 가능 수량 > 0** = 아직 살아 있는 주문 → **정정 금지, 재확인 대상**
      - rejected : 체결 0 · 취소 가능 0 (또는 주문번호 0 = 접수 실패) → 원가 청산(손익 0) 정정 가능
      - unknown  : 판단에 필요한 값이 없음 → 정정하지 않는다(추측 금지)
    """
    f = None if filled_qty is None else float(filled_qty)
    c = None if cancelable_qty is None else float(cancelable_qty)
    if f is None:
        return "unknown"
    if f > 0:
        return "filled"
    if c is None:
        # 체결 0 인데 취소가능 수량을 모르면 '거부'라고 단정할 수 없다 → 재확인
        return "resting" if order_no in (None, 0) else "unknown"
    if c > 0:
        return "resting"
    return "rejected"


def may_correct_as_unfilled(state: str) -> bool:
    """원가 청산(손익 0) 으로 정정해도 되는가 — **결말이 확인된 경우만** True."""
    return state == "rejected"


def needs_recheck(state: str) -> bool:
    return state in ("resting", "unknown")
