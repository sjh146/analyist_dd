---
name: quant-safety
description: "Use before market open and before exit windows."
---

# quant-safety — 실주문 안전 상시 감사

역할계획 v2 ④(신규). 근거: `docs/QUANT_ROLE_PLAN_V2.md` §3·§5.4.
경로 감사와 분리한 이유: 창 **전에** 돌아 사람에게 요청을 올리는 것이 목적이다(9/30 사고는 창 후 보고였다).

## 언제
- 평일 08:25 (개장 전 · preopen 틱 08:20 뒤)
- 평일 14:45 (close 진입창 14:50–15:29 **앞**)
- 미청산 포지션이 있으면 익일 09:00–09:10 청산 창 **앞**에 추가로

## 산출물
`reports/safety/<날짜>.json` — 통과/경보 + 사람 단계 요청(있으면)

## 게이트 (실행 가능: `scripts/audit_safety.py` — 미구현 시 수동 확인 후 '미검증' 표기)
1. 킬스위치 파일 경로·존재(trader-agent `kill_switch.txt`)
2. 한도 설정 실측 일치: 일 3건 · 종목 1/3 · 동시 3종목 · 총 100% · `kelly_fraction` · 청산 후 쿨다운 30분
   (정본 = `trader-agent/trader_core/config.py` — 보고서에서 숫자를 손으로 쓰지 말고 `scripts/risk_limits.py` 로 읽는다)
3. 페이퍼 검증 없는 실주문 경로 변경 0
4. **창 전 세션 생존**: 브리지 `/health` + `/balance` **둘 다** 응답(반쪽 세션 판정)
5. 미체결·슬롯 점유·`fees` 반영(0이면 '회계 미검증'으로 표기)
6. 브리지·루프 프로세스 각 1개(`run_bridge.py` + `run_market_loop.py`) 생존

## 권한
경보 + 창 전 **사람 단계 요청**(Creon 재로그인 등). 킬스위치 자동 발동·한도 변경은 **대표 승인**.

## 하지 않는 것
주문 실행 · 한도 변경 · 킬스위치 발동.
