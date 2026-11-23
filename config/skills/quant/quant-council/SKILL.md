---
name: quant-council
description: "Use when deciding strategy, priorities, or thresholds."
---

# quant-council — 전략회의 (주 1회, 대표+역할)

analyist_dd 역할계획 v2 의 ①. 근거: `docs/QUANT_ROLE_PLAN_V2.md` §3·§5.1.
gstack `/office-hours` 의 '결정 기록 + 되돌림 조건' 규약만 차용하고, 대상은 자원 배분·문턱·우선순위다.

## 언제
- 금 16:00 정기(대화형 — 사용자 참여가 필요하다)
- 사용자가 "이거 계속 할까 / 유지할까 / 뭐 먼저" 를 물을 때
- 감사관·안전 감사관 리포트가 정책 변경을 시사할 때

## 입력
- 지난 주 원장(`data/reports/{researcher,model_engineer,trader}_ledger.jsonl`) · trader-agent 거래 저널
- 게이트 비용(차단된 후보의 실측 등락) · `reports/audit/*` · `reports/safety/*`

## 산출물
`docs/council/<YYYY-MM-DD>.md` — 결정 1줄 + 근거 수치 + **되돌리는 조건** + `Supersedes:` + 승인 요청 목록

## 게이트
1. 수치 없는 주장은 '가설'로 표기(기억·인상으로 결정하지 않는다)
2. 결정마다 반증 가능한 되돌림 조건을 쓴다
3. 문턱·한도·계약 변경은 실행하지 않고 **승인 요청**으로만 올린다
4. 회고(I): 스킬·런북 갱신 항목을 회의록에 함께 적는다

## 하지 않는 것
코드 수정 · 실험 실행 · 승격/배포(실행 역할 소유).
