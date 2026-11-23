# 야간 개선 오케스트레이터 — 큐·디스패처·보호 경로

측정된 손실원에서 과제를 만들어 **worktree 위임 → 기계 검증 → 조건부 병합**을 매일 밤 사람 없이 돌린다.
설계 정본: 레포 `docs/ORCHESTRATOR_PLAN.md`. 자율성 등급·게이트 규칙은 `references/autonomy-objective.md`.

## 1. 병합 금지 경로 (디스패처가 경로 검사로 강제)

```
trader-agent/                                     # 실주문 경로 전체
scripts/full_pipeline_dd.sh                       # 라이브 ML 파이프라인
services/xgboost-ml/app/training/champion_promote.py   # 승격 게이트 본체
config/objective.json                             # 목표·봉투·게이트 '정책 값'
.env · kill_switch.txt                            # 자격증명·킬스위치
scripts/gate_promote_live_score.py · scripts/objective.py  # 게이트 임계·정책 CLI
```

변경 경로(`git status --porcelain`)가 이 목록에 닿으면 → `needs_human`(브랜치·보고만 남김).
프롬프트에도 같은 목록을 넣지만 **강제는 경로 검사**다 — 프롬프트 준수는 보장이 아니다.

## 2. 큐 (`data/state/improve_queue.json`)

```json
{"id": "Q<YYYYMMDD>-NN", "title": "...", "why": "<측정된 근거>",
 "acceptance": ["기계가 확인할 문장"], "protected": false,
 "priority": 1, "state": "todo|inflight|merged|rejected|needs_human",
 "attempts": 0, "max_attempts": 2, "commit": null, "note": null, "source": "objective_state.top_lever"}
```

규칙:
- **근거 없는 과제를 만들지 않는다.** seed 는 `objective_state.top_lever` 와 역할 원장의 `needs_setup`/`blocked`
  에서만 만든다. 근거가 없으면 큐를 비워 둔다(빈 큐 = 아무 것도 안 하는 밤).
- **중복 판정은 제목이 아니라 근거로**: 같은 `source`+`why` 면 제목 문구가 달라도 1건이다(제목만 비교하면
  사람이 제목을 다듬은 뒤 seed 가 같은 일을 다시 넣는다).
- `max_attempts`(2) 도달 시 `needs_human` 고정 → 무한 재시도 금지, 큐가 다시 선택하지 않는다.
- **수용 기준은 과제가 들고 있다**(사람이 아니라). 기계 검증이 가능하려면 여기가 명령으로 환산될 문장이어야 한다.

## 3. 디스패처 1회 = 과제 1건

1. `data/state/improve_pause` 있으면 즉시 종료(사람의 킬스위치).
2. seed → `todo` 중 priority 최소·attempts 여유 있는 1건 선택 → `claim`(inflight, attempts+1).
3. `git worktree add -b auto/<QID> <WT> master` → 그 안에서만 작업(위임 에이전트에게 **git 금지**).
4. 금지 경로 검사 → `verify_agent_worktree.sh` → 루트 `pytest` → (통과 시) worktree 에서 1커밋 →
   master 로 체리픽 → push. 게이트 OFF 면 **커밋까지만 하고 브랜치 보존**(검증만 보고).
5. 결과를 `data/reports/improve_runs.jsonl` 에 남긴다(`commit`·`revert: git revert <sha>` 포함).
6. 틱은 실행이 있을 때만 요약을 내보내고 없으면 침묵한다(no_agent).

## 4. 프롬프트에 반드시 넣는 것

금지 경로 목록 · "git 명령 금지/리포 밖 파일 금지" · 과제의 **수용 기준 원문** · "추측 금지, 못 구하면
측정 불가로 표기" · "상단 docstring 에 WHY 와 근거" · "검증 명령을 실제로 실행해 출력을 보고서에 붙여라".

## 5. 도입 단계와 예산

| 단계 | 자동 병합 | 목적 |
|---|---|---|
| dry-run (며칠) | 없음(검증·보고만) | 위임·검증·기록이 실제로 도는지 확인 |
| T0 자동 | 측정·테스트·문서·감사·틱 | 무인 개선이 커밋을 만드는지 확인 |
| T1 확대 | 라이브 파라미터 일부 | **별도 승인 + 되돌림 장치가 먼저 있을 때만** |

예산: 야간 최대 3과제 · 동시 1개(경쟁 실행 금지) · 과제당 timeout 45분.
받지 않는 것: 브리지·계좌 API, tailscale, `.env` 내용, 킬스위치, 크론 등록.
