# 야간 개선 오케스트레이터 설계 (④) — 자율 개선 루프의 마지막 조각

목적: `data/state/objective_state.json`(오늘의 목표·돈·봉투·1순위 손실원)을 읽고, **우선순위 큐에서
과제를 꺼내 위임 에이전트(Claude Code/OpenCode)에게 worktree 에서 시키고, 기계 검증을 통과한 것만
병합**하는 것을 매일 밤 사람 없이 돌린다. 어젯밤(10-02/03)에 손으로 한 흐름의 자동화다.

## 0. 경계 — 무엇을 자율로 병합하고, 무엇을 절대 병합하지 않는가

**자동 병합 허용(T0)** — 라이브 경로·위험 봉투를 건드리지 않는 것:
측정·계기판·감사 스크립트, 테스트, 문서, 크론 래퍼(신규 틱), 읽기 전용 도구, 게이트 *코드*(정책 값 제외)

**절대 자동 병합 금지(제안만)** — 아래 경로를 1줄이라도 건드리면 큐가 `needs_human` 으로 끝난다:
```
trader-agent/**                                   # 실주문 경로 전체
scripts/full_pipeline_dd.sh                        # 라이브 ML 파이프라인
services/xgboost-ml/app/training/champion_promote.py   # 승격 게이트 본체
config/objective.json                              # 목표·봉투·게이트 '정책 값'
.env · kill_switch.txt · scripts/gate_promote_live_score.py(임계) · scripts/objective.py(정책 CLI)
```
이 경우 에이전트는 브랜치·proposal 문서만 남기고, 오케스트레이터는 Discord 로 "승인 필요" 를 알린다.

## 1. 구성 요소

| 요소 | 파일/위치 | 역할 |
|---|---|---|
| 큐 | `data/state/improve_queue.json` | 과제 목록(우선순위·상태·담당·시도 횟수·근거) |
| 큐 채우기 | `scripts/improve_queue_seed.py` | objective_state 의 1순위 손실원 + AUTONOMY.md "안 된 것" + 역할 원장에서 과제 생성(중복 방지) |
| 디스패처 | `~/.hermes/scripts/improve_tick.sh` + `scripts/improve_dispatch.py` | 큐에서 1건 선택 → worktree 생성 → 위임 실행 → 검증 → 병합/기각 → 큐 갱신 → 보고 |
| 위임 | `scripts/ask_claude.sh`(1차 Claude Code → 실패 시 OpenCode 폴백) | worktree 안에서만 작업, git 금지 |
| 검증 | `scripts/verify_agent_worktree.sh` + 루트 `pytest` + 과제별 수용 기준 | 기계 판정(사람 판단 없음) |
| 기록 | `data/reports/improve_runs.jsonl` | 과제·프로토콜·결과·커밋·되돌리는 법 한 줄 |

## 2. 과제 명세(큐 항목 스키마)

```json
{
  "id": "Q20261006-01",
  "title": "청산창(09:00-09:10) 커버리지 감시를 preopen 감사에 통합",
  "why": "objective_state 1순위 손실원 -1,814원 · 근거 '중간'",
  "acceptance": ["tests/test_exit_window*.py 통과", "감사 JSON 에 exit_window 키 존재", "기존 감사 4종 회귀 없음"],
  "protected": false,
  "priority": 1,
  "state": "todo|inflight|merged|rejected|needs_human",
  "attempts": 0, "max_attempts": 2,
  "commit": null, "revert": null
}
```
**수용 기준을 사람이 아니라 과제가 들고 있다** — 검증이 기계적이려면 여기가 구체적이어야 한다.

## 3. 밤 사이 동작(디스패처 1회 = 과제 1건)

1. **선택**: `state=todo` 중 priority 최소, `attempts < max_attempts`. 없으면 조용히 종료.
2. **inflight 표시**(중복 실행 방지: 파일 락 + state).
3. **worktree 생성**: `git worktree add -b auto/Q20261006-01 /home/jhshi/wt/q1 master`.
4. **위임**: `ASK_CLAUDE_TIMEOUT=2400` 로 실행. 프롬프트에 **금지 목록(protected 경로)** 과
   수용 기준을 그대로 넣고, "git 명령 금지·리포 밖 파일 금지" 를 명시.
5. **검증(기계)**: ① `git status` 의 변경 경로가 protected 를 건드리면 즉시 `needs_human`
   ② `verify_agent_worktree.sh`(구문·테스트·보고서) ③ 루트 `pytest -q`
   ④ 과제의 acceptance 문구를 **명령으로 환산**해 실행(예: "tests/x 통과" → pytest 호출).
6. **판정**: 모두 통과 → master 로 체리픽(단일 커밋) + `git push`; 하나라도 실패 → `rejected`
   (사유·로그 경로 기록, `attempts+1`).
7. **기록**: `improve_runs.jsonl` 에 {"id", "branch", "commit", "revert": "git revert <sha>", "결과"}.
8. **보고**: 밤 요약을 Discord 로(과제·결과·소요·실패 사유). 정상적으로 할 일이 없으면 침묵.

## 4. 예산·안전

- 야간 **최대 3과제**, 동시 1개(경쟁 실행 금지 — 실측: 역할 틱이 겹치면 자원 경합).
- 과제 타임아웃 45분(`timeout 2700`), 전체 틱 상한 2시간.
- **킬스위치**: `data/state/improve_pause` 파일이 있으면 즉시 중단(사람이 만드는 파일 하나).
- 실패 2회 과제는 `needs_human` 으로 고정(무한 재시도 금지).
- 병합은 과제당 **1커밋** → 되돌리기는 `git revert <sha>` 한 줄(큐에 기록).
- 위임 에이전트에게 절대 주지 않는 것: 브리지·계좌 API, tailscale, .env 내용, 킬스위치, 크론 등록.

## 5. 큐 채우기(과제를 어디서 얻나)

1. `objective_state.json` 의 `top_lever`(+ 향후 top1~3) → 손실원 제거 과제
2. `docs/AUTONOMY.md` §5 "아직 안 된 것" → 남은 구조 과제
3. 역할 원장(`data/reports/*_ledger.jsonl`)의 `needs_setup`/`blocked` 항목 → 데이터·배선 과제
4. 위 세 곳에 없으면 **큐를 비워 둔다**(억지 과제 생성 금지 — 과제 없는 밤은 아무것도 안 한다)

## 6. 단계적 도입(권고)

| 단계 | 기간 | 자동 병합 범위 | 목적 |
|---|---|---|---|
| ① dry-run | 3일 | **없음**(검증만, 결과 보고) | 위임·검증 파이프라인이 실제로 도는지 확인 |
| ② T0 자동 | 1주 | T0(측정·테스트·문서·감사·틱) | 무인 개선이 실제 커밋을 만드는지 확인 |
| ③ 확대 | 이후 | T0 + (승인 후) T1 일부 | 라이브 파라미터까지 넓히려면 별도 승인 |

## 7. 결정이 필요한 것 (사람)

1. **자동 병합을 언제부터 허용할까** — ① dry-run 3일 먼저(권고) / 즉시 T0 자동 / T0+T1 자동
2. **야간 과제 수** — 3건(권고) / 1건 / 5건
3. **승인 필요 알림 대상** — Discord(현행) / 다른 채널 / 알림 없이 큐에만 적재
4. **어떤 저장소 범위** — analyist_dd 만(권고) / trader-agent 도 포함(권고 안 함: 실주문 경로)
