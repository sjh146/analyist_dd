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

## 현재 상태 (역할들이 갱신 · 리뷰보드 2026-09-25 22:50 검증)

### 리서처
- **R9 판정 실명 수리**(`build_event_features` 가 자기 커버리지를 안 갱신) → 이벤트 17/17 nonzero,
  alive/dead **147/52 → 164/35**. 커밋 `0c8335b` 푸시 완료. 검증: `dq_feature_feature_count` 199 실측.
- DQ 실측(내 재확인): `dq_claim_parse_failure` 9시리즈 전부 0 · `dq_asof_violation_rows` 4스코프 0 ·
  `dq_padding_ratio` 0 · `market_data` 최신일 **2026-09-23**(`krx_trading` 도 동일, KIS 일봉 로그
  "구간 9/18~9/24 = 4영업일") → freshness 2.0일은 **9/24·9/25 연휴 감안 정상**(warn 3).
- 남은 결함: feature_coverage **혼합 배치 128행**(09-24 값) · R7(휴장 캘린더 당일) · R11·R12·R13 미착수 ·
  R15(Alertmanager) 승인 대기 · R14 감시 유지.

### 모델엔지니어
- **정렬 버그(중복 라벨 14개)** — 독립 재확인: `panel_420_asofpatch.npz` feature_names **210개 중 14개 중복**
  (`cross_trend`·`price_volume`·`target_ma_5/10/20` 등, X shape 13609×210). 수정 코드(dedupe_names +
  폴드 하드 가드)는 컨테이너에 존재(L93·L269). **단 실행 중 U1 은 구코드**(스크립트 mtime 22:26 > 시작 21:32).
- 누수 게이트(수정 후 재측정): 단일피처 최대 분리도 **0.5637** (<0.75) 통과 · as-of 0 · 상장 전 패딩 0 ·
  시장레벨 27개가 폴드 top30 에 **0개** → 통과.
- **미해결 — 종목상수 비율의 정의가 두 개다(새 발견)**: 엔지니어는 패널 기준 **38/210**(=0.181),
  리서처 메트릭 `dq_feature_stock_constant_ratio` **0.3293 = 54/164**(살아있는 피처 기준, 54 = 패널
  null-dominated 수와 동일). 엔지니어의 "모집단 0.3293 대비 1.2~1.6배"는 **분모·정의가 섞인 비교**다.
  강한 정의(38/210)로는 과대표집이 2.0~2.9배로 커진다 → 다음 사이클에 정의 통일 후 재판정.
- **신규 실측 — 저녁 파이프라인 챔피언 재학습은 구조적으로 실패한다**: 20:29 KST 시작, 피처 빌드
  11,813쌍을 1.26쌍/s 로 9,400쌍(79.6%)까지 진행 후 **2시간 캡에 exit=124**. 소요 156분 > 캡 120분
  → 매일 같은 실패. `champion/auc.txt` **0.551318 동결**(9/23 15:18 이후), `champion_cand` 미생성.
- U1(150종목): 1차는 19:59:15 마지막 로그 기록 후 사망(20:00 틱이 기록, 저녁 파이프라인 Phase 0
  compose up 창과 일치) → 2차 21:29:40 재기동(load1=2.64, force 아님). 22:48 실측 **6,000/41,893
  @1.29쌍/s ETA 06:31**, 체크포인트 동작(`panel_150u.npz.meta.json` processed 6,000, 22:47 갱신)
  → 죽어도 재개로 대부분 보존된다.
- 미커밋: `scripts/wf_wave.py`·`scripts/wf_label_sweep.py`(수정) · `478ba3d` 미푸시.

### 트레이더
- **어제의 브리지 `connected:false` 는 해소됨** — Windows `127.0.0.1:8100/health` =
  `{"ok":true,"connected":true}` (pid 18996, 13:04:42 기동). 리뷰보드가 직접 확인.
- 그러나 **매매 루프 프로세스가 없다**(Windows python 프로세스 1개 = 브리지뿐) → 오늘 78사이클 전부
  `halted`, 체결·청산 0건. 9/24 밤 브리지 미연결 3회로 걸린 자기차단 래치가 13:07 에 풀렸지만
  루프를 다시 띄우지 않아 13:07~15:30 무감시.
- 저널 실측(리뷰보드 재계산, scoreboard 와 일치): 청산 **31건 · 승률 12/31 = 38.7% · 실현 -7,839원**
  (**fees 전부 0 = 수수료·세금 미반영**) · 보유 10종목 · 마지막 진입 9/21 14:52 (4.2일 전).
- 피드: 20:30 발행(close 20건 85.5~90.0 spread 4.5 경고 / swing 20건 51.4~66.1), `close_price` 누락 0,
  `feed_server` 8090 → 200.
- **불일치(확인 필요)**: swing 후보 `signal_date=2026-09-24` 는 **휴장일**인데, 같은 날 저녁 파이프라인
  Phase 3 은 "0 stocks" 를 저장했다 → swing 산출물 출처 확인 필요.
- 켈리: `screener_stats_measured.json` `usable=false`(close f*=-0.0267 ≤ 0) → 페이로드 미기록 유지(올바름).

### 열린 사람 단계 (리뷰보드 집계 — 중복 제거)
1. **⏰ 월(9/28) 08:35~08:40 — HTS(Creon) 로그인 + 브리지 재기동 (사람 직접, 대행 불가)**
   지금 브리지(pid 18996)는 금요일 세션을 물고 있어 주말을 못 버틸 가능성이 크다. 08:45 예약작업이
   새 브리지를 띄우면 8100 포트 점유로 죽고 낡은 세션 브리지가 `not_connected` 로 응답 → 오늘과
   같은 자기차단 재발. 순서: HTS 로그인 → 기존 브리지 종료 → `bridge_run_admin.bat`(관리자 콘솔).
   검증: `curl http://127.0.0.1:8100/health` 가 `connected:true` + `runner.log` 에 `halted` 아닌 사이클.
   ※ 예약작업(bridge 08:45·loop 08:50)은 **대화형 모드**라 로그온 상태가 필요하다.
   (기존 "브리지 재시작" 항목은 13:04 재기동으로 **해소** → 위 월요일 항목으로 대체)
2. **승인 필요 — Alertmanager 컨테이너 + Discord 웹훅(R15)**: 규칙 20개가 평가만 되고 통보 경로가 없다.
   웹훅 URL 만 사용자가 주면 리서처가 컨테이너를 세운다. 없으면 DQ 위반이 이 크론 보고에만 실린다.
