# SPEC: 경로별 동시보유 슬롯 배선 (스윙 3 / 단타 2, 전역 5) — 2026-10-07 사용자 승인

사용자 요청: "스윙용 최대보유 주식수 3개 유지하고 단타 스켈핑용 보유주식 총2개 까지 보유할 수 있게".

## 배경
- 규칙: `trader_core/slot_rules.py` (DEFAULT_SLOTS={"swing":3,"daytrading":2}), 설정 필드: `config.max_open_positions_by_screener`.
- 없던 것 = **엔진 배선**. 스윙 3종목이 전역 cap(3)을 다 써 단타가 진입 불가(실측: 미청산 3행 전부 swing).

## 구현 (이 커밋)
1. `config.py`: `max_open_positions` 3 → 5, **총 노출 0.30 불변**(원화 노출 불변, 5종목이면 종목당 ~6%).
2. `engine.py`: 진입 게이트 → `self._slot_allows(screener, ...)`. 경로별 보유수는 **저널**에서 센다
   (브로커 포지션엔 스크리너 귀속이 없다). 슬롯 미정의 경로(close)는 전역 cap 게이트가 대신 막는다.
   **fail-closed**: 저널 조회 실패 시 진입 차단.
3. 테스트: `test_slot_wiring.py` 신규 + 전역 3 고정 기존 테스트 3종 갱신. 검증 **483 passed**.

## 적용
트레이더 루프 **재기동** 시 반영(실주문 경로 배선 변경).
