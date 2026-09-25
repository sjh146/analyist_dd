# QUANT_BOARD — 퀀트 3역할 자율 협업 규약

작성 2026-09-25. 목적: **사용자가 프롬프트를 넣지 않아도** 리서처·모델엔지니어·트레이더가
서로의 산출물을 받아 이어서 일하게 한다.

## 역할 (스킬이 단일 정의 — 여기서는 권한과 핸드오프만 정한다)

| 역할 | 스킬 | 소유 파일 |
|---|---|---|
| 리서처 | `quant-researcher` | 수집기(`services/{kis,krx}-collector`, news-analyzer), `scripts/*collect*.py`, `scripts/kis_supply_*.py`, `scripts/feature_coverage_report.py`, `scripts/dq_claim.py`, `scripts/run_with_claim.py`, `config/prometheus/**`, `init-scripts/postgres/*` |
| 모델엔지니어 | `quant-model-engineer` | `services/xgboost-ml/**`, `scripts/wf_*.py`, `scripts/champion_*.py`, `scripts/pnl_backtest.py`, `scripts/train_curated.py`, `app/models/**` |
| 트레이더 | `quant-trader` | `scripts/feed_export.py`, `scripts/feed_server.py`, `scripts/build_screener_stats.py`, `data/feed/**`, `services/{strategy-agents,trade-executor}/**`, `trader-agent/FEED_CONTRACT.md`(읽기) |
| 오케스트레이터 | `quant-review-board` | `docs/QUANT_BOARD.md`(이 문서), 보고 |

## 자율 실행 구조 (Hermes 크론)

역할 간 전달은 **크론의 `context_from` 체인**이 담당한다(앞 역할의 최신 산출물이 다음 역할의
컨텍스트로 주입된다). 사람이 중계하지 않는다.

```
리서처(평일 21:10)
   └→ 모델엔지니어(평일 22:00, context_from=리서처)
          └→ 트레이더(평일 08:20, context_from=모델엔지니어)
                 └→ 리뷰보드(매일 22:40, context_from=리서처+엔지니어+트레이더)
```

각 역할은 자기 실행 결과를 이 문서의 **"현재 상태"** 절과 아래 핸드오프 로그에 남긴다.

## 하이브리드 위임 구조 (3안 채택, 2026-09-25)

역할 분담을 명확히 나눈다:

| 계층 | 담당 | 하는 일 |
|---|---|---|
| 실행·스케줄·게이트·검증·보고 | **Hermes 크론** (위 5개 잡) | 순서·전달(`context_from`)·자기신고 검증·DB/메트릭 실측 확인 |
| **지적 작업** | **Claude Code + gstack** (`scripts/ask_claude.sh`) | 코드 리뷰(`/review`), 근본원인 조사(`/investigate`), 스펙(`/spec`), 세션 메모리(`/learn`) |
| 역할 정의 | 양쪽 미러 | `~/.hermes/skills/quant/*` 와 `~/.claude/skills/quant-*` 동일 내용 |

**위임 방법** (결과는 항상 파일로 떨어진다):
```bash
cd /home/jhshi/analyist_dd
./scripts/ask_claude.sh review      reports/claude_review_<대상>.md  "<무엇을 검토할지 + 근거 요구>"
./scripts/ask_claude.sh investigate reports/claude_inv_<대상>.md      "<증상 + 반증 방법 요구>"
./scripts/ask_claude.sh spec        docs/spec_<기능>.md               "<요구사항>"
```
- 백엔드는 **DeepSeek**(`claude-ds` 래퍼). 비전·MCP tool-use 블록은 미지원.
- 타임아웃 기본 900초(`ASK_CLAUDE_TIMEOUT` 로 조정).

**자동 폴백 (2026-09-25 배선)**
1차 Claude Code 가 실패(비정상 종료 또는 출력 40바이트 미만)하면 **opencode 로 자동 전환**한다
(`opencode run --pure --dir <proj>`, review/investigate 모드는 `--agent explore` = 읽기 전용,
`--auto` 미사용 → `opencode.json` 의 deny 목록 `.env` 읽기 금지·webfetch 금지 유지).
어느 백엔드가 답했는지는 **`$OUT.backend`** 에 기록된다(`backend=claude|opencode|none`).
둘 다 실패하면 위임 없이 본업을 계속한다 — 위임은 보강이지 의존이 아니다.

**위임 결과 취급 규칙 (중요)**
1. 위임 결과는 **자기신고와 동급**이다 — 파일을 읽어 근거(파일:라인, 명령 출력)를 확인한 뒤에만
   채택한다. 확신도 '낮음' 항목은 채택하지 않고 별도 기록만 한다.
2. 위임이 실패해도(타임아웃·빈 출력) **역할의 본업은 계속한다** — 위임은 보강이지 의존이 아니다.
3. 위임은 **읽기 전용**을 기본으로 한다(프롬프트에 "수정 금지"를 넣는다). 수정은 해당 파일
   소유 역할만 한다.

## 핸드오프 계약 (말로 된 승인 금지 — 파일·수치 증거 필수)

1. **리서처 → 모델엔지니어**: 품질 성적표 — `dq_col_quality_null_ratio`, `dq_padding_ratio`,
   `dq_asof_violation_rows`, 러너 자기신고(`dq_claim_parse_failure`/`dq_claim_gap`), 커버리지,
   신규 피처의 선별문턱(≈0.044) 대비 edge. **통과 못 하면 피처로 제안 금지.**
2. **모델엔지니어 → 트레이더**: 검증 성적표 — 폴드별 AUC·평균±std·**폴드 승률**, purge 적용,
   승격 dry-run 결과, 대시보드용 `dq_feature_*`.
3. **트레이더 → 리서처**: 발행 로그(건수·`close_price` 누락·점수 분포), 계약 검증, 게이트 통과
   증거, 그리고 **어떤 피처/데이터가 실제 기여했는지** 환류.

## 권한 (사용자 승인 없이 가능 / 불가)

**가능**
- 수집·점검·재실행, 품질 메트릭 조회, 패널/백테스트 측정, 승격 **게이트 통과 시에만** 승격
- 피드 발행·재발행, 계약 검증, 켈리 사전확률 파일 생성, dry-run 스캔
- 자기 소유 파일 수정, 문서·로그 기록, 이 보드 갱신

**불가(사람 단계로 에스컬레이션)**
- Creon 로그인·브리지 재시작(관리자 권한 — 사용자만 가능)
- 실계좌 주문 경로 활성화 (현재 계좌는 **모의투자**, 실주문은 모의계좌까지 허용)
- 운영 DB 스키마 파괴적 변경, 크론 파일 수정, 다른 역할 소유 파일 수정
- 승격 게이트 우회(기준선·바닥값 임의 변경)

## 파일 소유권 (동시 편집 충돌 방지 — 2026-09-24 실사고)
형제 에이전트 2개가 같은 파일(`supply_collector.py`)을 동시에 써서 한쪽 작업이 소실됐다.
- 역할 간 **디렉터리 단위 소유권 유지**, 공유 리더(`feature_engine/*.py`)는 모델엔지니어 단독
- 쓰기 전 mtime 확인, 같은 파일이 필요하면 **직렬화**

## 보고 형식 (사용자에게) — **사람 단계를 빠뜨리지 않는 것이 핵심**

모든 역할의 보고는 아래 3부를 **항상** 포함한다(사람이 할 일이 없어도 "없음"이라고 쓴다).
빈 칸은 "확인 안 함"과 구분되지 않으므로 금지.

### ① 내가 할 일 (자율 진행 중)
승인 없이 진행하는 것. "진행 중" 또는 "예정(언제)".

### ② 사용자 승인이 필요한 일
내 권한 밖이라 승인을 받아야 하는 것(게이트 기준선 변경, 스키마 변경, 실주문 활성화,
크론 수정, 다른 역할 소유 파일 수정). 형식:
```
- [무엇을] 무엇을 바꾸려는가
  왜: 실측 근거(수치)
  안 하면: 방치 시 구체적 손해
  승인하면: 내가 실행할 정확한 명령/절차
  긴급도: 언제까지 필요
```

### ③ 🙋 사람이 직접 해야 하는 일 (대행 불가)
브리지/Creon 재시작, UAC, HTS 조작 등. 형식:
```
- [무엇을] / 왜 / 정확한 명령 한 줄 / 완료 후 내가 검증할 방법 / 마감 시각
```

우선순위는 **③ → ② → ①** (사람이 직접 해야 하는 것이 다른 모든 것을 막는다).
마감 임박 항목은 제목에 `⏰`. 그 아래에 실측 수치를 붙인다(추정은 추정이라 표시).

**집계는 리뷰보드(22:40)가 담당**: 세 역할이 올린 사람 단계를 중복 제거·우선순위 정리해
하나의 목록으로 만들고, 이미 해결된 항목은 빼고 그 사실을 적는다.

---

## 현재 상태 (역할들이 갱신)

### 리서처
- 러너 자기신고 배선: `kis_supply_backfill`·`kis_supply_extend_history` 인라인 + 범용 래퍼
  `scripts/run_with_claim.py` (나머지 러너는 래퍼로 코드 수정 없이 감쌀 수 있음)
- 정확성 메트릭 6종 가동(`dq_*`), 알림 `dq_accuracy` 9개
- 미배선: 크론 래퍼(`/home/jhshi/cron/*.sh`)에 래퍼 적용

### 모델엔지니어
- **2026-09-25 챔피언 승격**: `20260814` → `20260923` (auc.txt 0.6131 → 0.5513),
  백업 `champion_prev_20260924-224249`, 기준선 `robust_auc.json` 기록(metric=ensemble_auc)
- 게이트: `min_auc`/`legacy_baseline_cap` 0.55 → **0.53** (3곳) + 지표 동형성 가드
- 실질 로버스트 상한 ≈0.54 확정 (as-of 누수 제거 후에도 불변) — `docs/asof_measurement_20260925.md`

### 트레이더
- 피드 경로 검증: WSL `feed_server`(8090) → Windows `127.0.0.1:8090` 도달 200 ✓
- 켈리 사전확률 생성기 `scripts/build_screener_stats.py` (실측 백테스트 기반, 보수적 축소)
- 미해결: `scoring_summary` 미포함(측정 데이터 대기), 브리지 `connected:false`(사람 단계)

### 열린 사람 단계
1. **브리지 재시작** — 관리자 콘솔에서
   `Get-NetTCPConnection -LocalPort 8100 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }`
   후 `C:\Users\jhshi\analyist_dd\trader-agent\bridge_run_admin.bat` (UAC). WSL 기동은 중간 무결성이라 붙지 않음(실측).
