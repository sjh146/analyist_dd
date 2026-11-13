"""경로별 동시보유 슬롯 규칙 — 스윙 3 / 단타 2 (2026-10-07 사용자 요청).

WHY 별도 모듈인가: 슬롯 배선 전 한도는 `config.max_open_positions` **전역 3** 하나뿐이라
"스윙 3 종목을 들고 있어도 단타 2 종목까지는 들어갈 수 있다"를 표현할 수 없었다
(전역 cap 은 2026-10-07 슬롯 배선에서 5 로 복원).
그리고 이건 **실주문 경로의 진입 판정**이라, 규칙을 순수 함수로 떼어내 테스트로 고정한 뒤
엔진에 배선한다(검증 없이 엔진을 고치지 않는다).

설계(위험 불변): 총 노출 상한(`max_invested_pct = 0.30`)은 건드리지 않는다.
슬롯은 '**최대 몇 종목까지**'만 정하고, 실제 진입은 기존 예산 게이트가 그대로 막는다.
→ 스윙이 30% 예산을 다 쓰면 그날 단타는 0건(설계대로), 예산이 남으면 단타가 슬롯 내에서 회전.
"""
from __future__ import annotations

from typing import Dict, Mapping, Optional

DEFAULT_SLOTS: Dict[str, int] = {"swing": 3, "daytrading": 2}
# close 경로는 별도 슬롯을 두지 않는다 — 전역 cap 을 그대로 쓴다(현행 유지).
GLOBAL_FALLBACK_SCREENERS = ("close",)


def slot_cap(screener: Optional[str], global_cap: int,
             slots: Optional[Mapping[str, int]] = None) -> int:
    """해당 경로에 적용할 슬롯 상한. 슬롯이 정의되지 않은 경로는 전역 cap."""
    table = dict(DEFAULT_SLOTS)
    if slots:
        table.update(slots)
    if screener and screener in table:
        return int(table[screener])
    return int(global_cap)


def can_enter(screener: Optional[str], open_by_screener: Mapping[str, int],
              global_open: int, global_cap: int,
              slots: Optional[Mapping[str, int]] = None,
              planned_same_screener: int = 0) -> bool:
    """이 경로로 지금 진입 가능한가 — ① 경로 슬롯 ② 전역 cap 둘 다 통과해야 한다."""
    cap = slot_cap(screener, global_cap, slots)
    used = int((open_by_screener or {}).get(screener or "", 0)) + max(0, int(planned_same_screener))
    if used >= cap:
        return False
    return global_open < int(global_cap)


def reason(screener: Optional[str], open_by_screener: Mapping[str, int],
           global_open: int, global_cap: int,
           slots: Optional[Mapping[str, int]] = None) -> str:
    """막힌 이유를 사람이 읽게 남긴다(게이트 문구와 동일 목적)."""
    cap = slot_cap(screener, global_cap, slots)
    used = int((open_by_screener or {}).get(screener or "", 0))
    if used >= cap:
        return "{0} 슬롯 포화({1}/{2})".format(screener, used, cap)
    if global_open >= int(global_cap):
        return "전역 동시보유 포화({0}/{1})".format(global_open, global_cap)
    return "진입 가능"
