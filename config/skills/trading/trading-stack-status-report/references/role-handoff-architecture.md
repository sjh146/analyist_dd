# 역할 간 통신 구조 (analyist_dd) — "페르소나들이 어떻게 통신하나" 질문용

메시지 버스·큐는 없다. 전부 **파일·DB + 크론 스냅샷 주입**이다. 답할 때는 아래 6계층을 그대로 나열하고,
한계(폴링·계약이 산문)를 함께 적는다 — 이 구조의 약점이 곧 개선 백로그다.

## 1) 크론 `context_from` 체인 (1차 인계)

`~/.hermes/cron/jobs.json` 의 각 잡 `context_from` 필드가 직전 역할의 **최신 출력 스냅샷**을 프롬프트에 주입한다.

```
리서처(평일 21:10) → 엔지니어(22:25, context_from=리서처) → 트레이더(08:20, context_from=엔지니어)
리뷰보드(22:40) = context_from [리서처, 엔지니어, 트레이더]
```

확인 명령:
```bash
/usr/bin/python3 -c "import json;d=json.load(open('$HOME/.hermes/cron/jobs.json'));
[print(j.get('name'), '<-', j.get('context_from')) for j in (d if isinstance(d,list) else d.get('jobs',[])) if j.get('context_from')]"
```

## 2) 공유 산출물 (주 교환 매체)

| 종류 | 경로 |
|---|---|
| 백로그 | `docs/QUANT_MODEL_BACKLOG.json` · `QUANT_RESEARCH_BACKLOG.json` · `QUANT_TRADER_BACKLOG.json` |
| 원장(append-only) | `data/reports/{model_engineer,researcher,trader}_ledger.jsonl` |
| 발견 요약 | `docs/QUANT_FINDINGS.md` |
| 규약·현황 | `docs/QUANT_BOARD.md` (프로토콜 + 핸드오프 로그) |
| 북극성 지표 | `docs/QUANT_SCOREBOARD.md` · `data/reports/quant_scoreboard.json` |

## 3) 핸드오프 계약 (방향별 필수 증거)

원칙: **말로 된 승인 금지 — 파일·수치 증거 필수. 자기신고는 검증 전까지 신뢰하지 않는다.**

- 리서처 → 엔지니어: 품질 성적표(`dq_asof_violation_rows`·`dq_col_quality_null_ratio`·커버리지·선별문턱 0.044 대비 edge). 통과 못 하면 피처 제안 금지.
- 엔지니어 → 트레이더: 검증 성적표(폴드별 AUC·평균±std·폴드 승률·purge·승격 dry-run).
- 트레이더 → 리서처: 발행 로그(건수·`close_price` 누락·점수 분포) + 피드 계약 검증.
- 환류(트레이더→백로그): `thesis_verdicts`·`loss_feedback` 의 `reason_class` ∈ {signal, timing, sizing, cost, regime}.
- 중복 방지: `from_trader`·`source`·`provenance` 키.

기계 검증: `docs/HANDOFF_CONTRACT.json` + `scripts/handoff_contract_check.py` (Hermes 크론 `handoff-contract-nightly`, 매일 03:20, deliver=local).
정상이면 완전 침묵, FAIL 만 exit 2. 백로그 항목의 상태별 필수 필드(가설·재현명령·수치 합격기준·차단사유·결과)와
Neo4j writer/reader 관계 쌍을 함께 검사한다.

## 4) 실행 조정 (서로 밟지 않게)

- 교차 락: `data/reports/{me_cycle,res_cycle,tr_cycle}/running.pid` → 다른 역할이 돌면 "시작 보류" (CPU 4코어 직렬화).
- 부하 가드: `load1 > 6` 이면 양보.
- 시각 분산: 엔지니어 `:07`, 리서처 `:47`, 트레이더 `:25` — 정각·30분 회피(Hermes 가 CLI+gateway 두 프로세스일 때 fire_claim TTL 300s 를 넘겨 서로 실행을 끊던 사고 대응).

## 5) 외부 위임 (보강 계층)

`scripts/ask_claude.sh {review|investigate|spec}` → 결과는 항상 파일(`reports/claude_*.md`, `docs/spec_*.md`).
1차 Claude Code(DeepSeek 백엔드) 실패 시 `opencode` 폴백, 둘 다 실패해도 본업 계속. **위임 결과는 자기신고와 동급** —
파일:라인 근거를 확인한 뒤에만 채택한다.

## 6) 사람·오케스트레이터

리뷰보드가 3역할 산출물을 읽어 승인/반려, CEO 틱(23:45)이 돈 축 우선순위를 정한다.
Discord 전달은 크론 `deliver` 경유(현재 전면 `local` 로 음소거: `jobs.json` 의 `deliver` 를 일괄 변경 + mtime 스탬프로 즉시 반영, 복원 정보는 백업/
`deliver_restore_manifest.json`).

## 한계 (반드시 함께 말한다)

1. 폴링 구조 — 인계 지연이 크론 주기(수 시간)에 묶인다.
2. 계약이 산문 문서 + 각 사이클 코드로만 존재 → 기계 검증 불가(그래서 3계층 검증기를 신설했다).
3. `context_from` 은 최신 스냅샷만 — 스레드·이력 없음.
4. 체인 단방향 + 예외적 환류(백로그 경유).
