---
name: quant-protocol-lock
description: "Use before an experiment or release starts."
---

# quant-protocol-lock — 계획 락 + 큐 심사

역할계획 v2 ②. 근거: `docs/QUANT_ROLE_PLAN_V2.md` §3·§5.2.
gstack `/plan-eng-review`(EM 모드)의 발상을 퀀트 계약으로 옮긴 것 — 역할이 아니라 **모드**다.

## 언제
- 백로그 항목을 `pending` 으로 올리기 직전
- 릴리스 요청(챔피언·피드 계약·크론·이미지) 접수 시
- 평일 15:55 큐 심사

## 산출물
`docs/locks/<id>.lock.json` — 목표 · 대조군 · 성공 문턱 · 폴드·시드 · est_minutes · timeout ·
표본 출처 · 데이터 소스별 러너·스케줄 표 · 롤백. 권한: **락 없는 착수는 거부**한다.

## 게이트 (실행 가능: `scripts/audit_protocol_lock.py` — 미구현 시 수동 확인 후 '미검증' 표기)
1. `counterfactual`·`success`·`est_minutes`·`timeout` 4필드 존재
2. est_minutes 는 **직전 런 실측 cell/min** 에서 계산(낙관값 금지)
3. 표본 유니버스 재현성(같은 함수 4회 교집합 100%)
4. 신규 데이터 의존이면 **러너·스케줄 표**(없으면 '수동 백필'로 표기)
5. **문턱이 사전등록 값과 일치** — 실측 위반: `full_pipeline_dd.sh` `--min-improvement 0.0` vs 사전등록 +0.02 → MT116
6. **큐 심사(H)**: 핸드오프 미소비 목록을 **소비 계획**과 함께 역할에 배정(실측: 생성 18 / 미소비 15)

## 하지 않는 것
실험 실행·코드 수정. 설계 판단 검토(락은 4필드+문턱만 본다 — 병목이 되면 큐가 굶는다).
