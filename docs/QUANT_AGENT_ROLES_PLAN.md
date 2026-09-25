# analyist_dd 에이전트 역할 하네싱 계획 (퀀트모델엔지니어 / 리서처 / 트레이더)

작성 2026-09-25. 근거는 전부 실측·소스 확인이며 추정은 추정이라고 표시했다.

## 0. 결론 요약

- **gstack 을 그대로 쓰는 것은 불가능하다.** 이유 3가지(전부 소스/환경 확인):
  1. gstack 의 역할 카탈로그에 **퀀트 역할이 없다** — CEO/EM/Designer/QA/CSO/Release Engineer 등 **SWE 역할 23개**다. 우리가 원하는 퀀트모델엔지니어·리서처·트레이더는 직접 작성해야 한다.
  2. **Hermes 호스트는 full install 이 없다.** `hosts/hermes.ts` 주석 원문:
     `No full install arm — users can hand-copy the instruction-only digest (setup's explainer arm prints this path; never auto-copied).`
     → Hermes 는 2KB 방법론 digest 수준만 받는다(스킬 자동 설치 없음).
  3. 이 머신에 **Bun·Claude Code·Codex 가 없다**(실측: opencode 1.18.32 만 설치). gstack `./setup` 은 Bun v1.0+ 를 요구하고, `/codex`·`/claude-code` second-opinion 스킬은 해당 CLI 가 필요하다.
- **대신 gstack 은 "설계 템플릿 + 방법론 소스"로 유용하다.** 특히 `hosts/hermes.ts` 가 툴 이름을 이미 번역해 둔 점이 중요하다:
  `'use the Agent tool': 'use delegate_task'`, `'use the Bash tool': 'use the terminal tool'`, `extraPathRewrites: CLAUDE.md → AGENTS.md`.
- **실행 경로는 2개를 병행한다(단일 소스: Markdown 역할 정의)**:
  - **Hermes 네이티브**(권장 주 경로): 역할을 Hermes 스킬로 작성 + `delegate_task` 로 격리 실행. 이미 `autonomous-ai-agents` 스킬군(opencode/codex/delegation)이 오케스트레이션 계층을 제공한다.
  - **opencode 네이티브**(부 경로): **OpenCode 는 gstack full install 대상**이므로 `./setup --host opencode` 로 gstack 방법론 스킬을 그대로 받을 수 있다(Bun 설치 필요). 역할은 `oh-my-openagent.json` 에 추가한다 — **이미 14개 에이전트가 정의돼 있다**(build/plan/prometheus/momus/oracle/librarian/atlas…).

## 1. 현황 자산 (실측)

| 자산 | 내용 | 역할 매핑 |
|---|---|---|
| `oh-my-openagent.json` | opencode 용 에이전트 14개 + categories 5 + subagents 5 + plugins(pontail `/plans` `/evidence` `/status`) + commands(monitor/pontail/cleanup-zombies), model `deepseek-v4-flash` | 하네싱의 기존 골격 |
| `~/.config/opencode/opencode.json` | 모델 `deepseek-v4-pro`, autoupdate | 실행기 설정 |
| `services/xgboost-ml` + `scripts/wf_wave.py`·`champion_promote.py`·`champion_robust_eval.py`·`full_pipeline_dd.sh` | 학습·검증·승격 게이트 | **퀀트모델엔지니어** |
| `scripts/feature_diagnosis.py`·수집기(kis/krx/dart/news)·`docs/*_PLAN.md`·`docs/공매도_데이터_소스_조사.md` | 데이터 출처·피처 진단 | **리서처** |
| `services/strategy-agents`·`trade-executor`·`feed_export.py`·`feed_server.py`·`docs/TRADER_INTEGRATION.md` | 전략·주문 경로·피드 계약 | **트레이더** |
| `~/.hermes/skills/autonomous-ai-agents/*` | opencode/codex/claude-code 위임 절차 | 오케스트레이션 계층 |
| Hermes 런타임 | `skill_manage`(절차 기억), `delegate_task`(격리 서브에이전트), `cronjob_manage`(예약) | 역할 배포 채널 |

## 2. gstack 에서 빌려올 것 / 버릴 것

**빌려올 것**
- **역할=Markdown 스킬** 패턴과 스프린트 파이프라인 `Think → Plan → Build → Review → Test → Ship → Reflect` (스킬이 다음 스킬의 입력 문서를 만든다).
- **게이트 스킬의 발상**을 퀀트로 번안:
  - `/plan-eng-review`(아키텍처 락) → **모델 검증 계획 락**(라벨·분할·purge·지표 사전 등록)
  - `/review`(prod 에서 터질 버그) → **누수 감사**(종목상수·NaN 커버리지·시장레벨·미래정보)
  - `/cso`(보안) → **주문 경로 안전 감사**(실거래/페이퍼 분리, 한도, 킬스위치)
  - `/learn`(프로젝트 기억) → **Hermes 스킬 갱신**으로 실패/함정 축적
  - `/careful`·`/freeze`·`/guard` → **파괴적 명령 가드 + 파일 스코프 락**
- **digest 의 행동 규칙**(reuse ladder, root-cause, user sovereignty) → `AGENTS.md` 에 추가(설치 불필요).
- **host config 시스템**(`defineHost()` 1파일로 호스트 추가) — 필요해지면 `analyist-dd` 호스트를 만들어 우리 역할을 생성 대상으로 넣을 수 있다(단 Bun 필요).

**버릴 것(우리에게 무의미)**
- 디자인/브라우저/QA/iOS/배포 계열 스킬, `/ship`·`/land-and-deploy`(우리 배포는 Docker+크론), second-opinion 의 Claude/Codex 의존(→ opencode 로 대체).

## 3. 역할 정의(초안)

### 3.1 퀀트모델엔지니어 (quant-model-engineer)
- **소유 파일/영역**: `services/xgboost-ml/app/training/**`, `app/models/**`, `scripts/wf_*.py`, `scripts/champion_*.py`, `scripts/full_pipeline_dd.sh`
- **입력**: 리서처의 데이터/피처 제안서, 트레이더의 성능 요구
- **해야 할 일**: 라벨·분할·purge·지표를 **사전 등록**하고 확장창 walk-forward(다중 폴드×다중 시드)로만 판정. 폴드별 짝비교와 승률을 함께 보고. 단일 분할 값은 판정에 쓰지 않는다.
- **게이트(하드)**: ① 사전 등록 문턱 미달이면 "개선"이라 쓰지 않는다 ② 테스트 AUC > 0.75 단일피처·종목상수 피처 발견 시 중단 ③ 승격은 `champion_promote --dry-run` 통과 후에만.
- **산출물**: `reports/overnight/*.jsonl` + 요약 JSON + 판정 근거(폴드 표).
- **금지**: 운영 DB 스키마 변경, 크론 수정, `champion/` 직접 덮어쓰기.

### 3.2 리서처 (quant-researcher)
- **소유 영역**: 수집기(`services/kis-collector`, `krx-collector`, `news-analyzer`, `yfinance-collector`), `scripts/*collect*.py`, `scripts/feature_diagnosis.py`, `docs/*_PLAN.md`
- **해야 할 일**: 새 데이터 소스의 **승인 상태·단위·커버리지·시점정합**을 1콜 프로브로 확정하고, 피처의 단변량 edge 를 실측해 선별 문턱과 비교. "채웠다"가 아니라 "선별에 들어가는가"로 보고.
- **게이트(하드)**: ① 출처 공시 시점(as-of) 확인 — 공시일 없으면 보수적 지연을 명시 ② 커버리지 NaN 을 0 으로 세지 않는다(null≠0) ③ 시장 전체 동일값 피처는 횡단면 모델 피처로 제안하지 않는다.
- **산출물**: 소스 계약서(엔드포인트·TR·필드·단위·한도), 커버리지 표, edge 표.

### 3.3 트레이더 (quant-trader)
- **소유 영역**: `services/strategy-agents`, `services/trade-executor`, `scripts/feed_export.py`, `scripts/feed_server.py`, `data/feed/**`, `docs/TRADER_INTEGRATION.md`
- **해야 할 일**: 피드 계약(필드·필수값·신선도·전략별 청산) 준수 확인, 발행물 원자적 교체, 점수 분포·퇴화 가드 확인, **실거래/페이퍼 경로 분리**와 킬스위치 점검.
- **게이트(하드)**: ① 실주문 경로 변경은 페이퍼 검증 없이 금지 ② 피드 계약 위반 후보는 발행 전 차단 ③ 킬스위치·한도 확인 없이 주문 경로 활성화 금지.
- **산출물**: 발행 로그(건수·가격 누락·점수 분포), 게이트 통과 증거, 페이퍼 결과.

### 3.4 오케스트레이터 (quant-review-board)
- gstack `/autoplan` 의 퀀트판. 순서: **리서처 제안 → 모델엔지니어 검증계획 락 → 트레이더 실행안전 → (실행)**.
- 각 단계는 **파일 산출물**을 남기고 다음 단계가 그것을 검증한다(말로 된 승인 금지).
- Hermes 구현: `delegate_task` 로 역할별 서브에이전트 격리 실행 + 결과를 부모가 검증.

## 4. 작업 단계 (예상)

| 단계 | 내용 | 산출물 | 예상 |
|---|---|---|---|
| P0 | `opencode run` 스모크 + `oh-my-openagent.json` 백업 + 역할 파일 위치 확정 | 스모크 로그 | 30분 |
| P1 | 역할 3종 + 오케스트레이터를 **Hermes 스킬**로 작성(게이트·금지·증거 형식 포함) | `~/.hermes/skills/quant/{quant-model-engineer,quant-researcher,quant-trader,quant-review-board}/SKILL.md` | 2~3시간 |
| P2 | 같은 정의를 **opencode 에이전트**로도 등록(`oh-my-openagent.json` 확장 + `.opencode/agent/*.md`) | opencode 에서 `--agent quant-*` 동작 | 1~2시간 |
| P3 | 게이트를 **실행 가능한 검사**로 구현(누수 감사·승격 dry-run·피드 계약 검사) | `scripts/quant_gate_{leakage,promote,feed}.py` | 반나절 |
| P4 | 예약 실행(cron): 트레이더=개장 전, 모델엔지니어=주 1회 walk-forward, 리서처=수시 | Hermes cron + 로그 | 1시간 |
| P5 | (선택) Bun 설치 → `./setup --host opencode` 로 gstack 스킬 설치, digest 를 `AGENTS.md` 에 추가 | gstack 스킬 + AGENTS.md | 1시간 |

## 5. 리스크 (오늘 실측에서 나온 것 포함)

1. **동시 편집 충돌** — 오늘 실제로 발생: 형제 서브에이전트가 같은 파일(`supply_collector.py`)을 동시에 써서 한쪽 작업이 덮였다. → **역할별 파일 스코프를 겹치지 않게 배정**하고, 쓰기 전 mtime 확인 + 스코프 락(`/freeze` 상당)을 규칙으로 둔다.
2. **역할이 기존 스크립트를 재구현** — 스킬은 반드시 기존 엔트리포인트(`wf_wave.py`, `feed_export.py` 등)를 호출하게 쓴다.
3. **비용** — 역할별 위임은 토큰을 쓴다. cron 역할에는 호출/토큰 예산 상한을 코드로 강제.
4. **Bun/Claude Code 부재** — gstack full suite·second-opinion 사용 불가. opencode 를 second-opinion 실행기로 쓰거나 Bun 을 설치(사용자 결정).
5. **가짜 승인** — 게이트를 산문으로 두면 통과했다고 주장만 하게 된다. P3 에서 **실행 가능한 검사**로 만드는 이유다.

## 6. 지금 필요한 결정

1. **Bun 설치 + gstack opencode full install 을 할지** (gstack 방법론 스킬을 그대로 받음) vs **Hermes 네이티브 역할 스킬만** 작성할지.
2. 역할 정의를 **레포에 둘지**(`docs/`, 팀 공유) vs **개인 Hermes 스킬**에 둘지.
3. 트레이더 역할에 **실거래 권한을 줄지**(현재는 페이퍼/피드까지만 권장).

---

## 7. 확정된 역할 분할 (2026-09-25 사용자 정의 반영)

세 역할로 나눈다. **repo 의 서비스 경계와 정확히 일치**하며, 리서처의 "감시"는 새로 만드는 것이 아니라 **이미 가동 중인 모니터링을 완성**하는 일이다(실측: prometheus scrape 6타겟 전부 `up`, grafana 대시보드 5개, 알림 규칙 7개 로드됨).

### 7.1 리서처 = 수집 + **데이터 품질 계약**
- 소유: `services/{kis,krx}-collector`, `news-analyzer`, `yfinance-collector`, `scripts/*collect*.py`, `scripts/data_gap.py`, `config/prometheus/**`, `config/grafana/dashboards/data_quality_signals.json`
- 임무: ① 더 좋은 품질의 데이터 소스를 찾아 승인·단위·커버리지·시점정합을 확정 ② 수집 파이프라인을 모니터링으로 감시 ③ **"수집 성공"이 아니라 "선별에 들어갈 품질"을 증명**
- 이미 있는 감시(재사용): `data_quality_signals`(신선도·0거래량·동결·dead feature·idle-in-transaction), `kis_data_monitoring`(시장별 종목수 갭·최신 거래일·분봉 수집시각)
- **공백 — 오늘 실패 유형 4개가 전부 "행도 값도 있는데 틀린" 유형이라 정확성 메트릭이 없다**:
  | 필요한 메트릭 | 오늘 근거 |
  |---|---|
  | `nan_not_zero` (NaN 을 0으로 세지 않음) | SNS `col!=0` 이 NaN 을 True 로 세어 커버리지 0.99 로 착시 |
  | `asof_violation_count` | 재무 피처가 종목당 값 1개 = 최신 스냅샷을 과거 행에 적용(최대 10개월 누수) |
  | `feature_stock_constant_ratio` | 상수 피처 15+개가 top30 선별을 지배 |
  | `feature_market_level_constant_count` | 날짜 내 분산 0 피처 34개(파생·프로그램·매크로) |
  | `padding_row_ratio` | 상장 전 구간을 all-zero 행으로 패딩(139행) 받음 |
  | 러너 자기신고 vs 실제 적재량 | 확장 러너가 `+0행`으로 성공 종료 |
- 기존 알림 7개: `MarketDataStale>3d`, `MarketDataDailyRowsDropped`, `MarketDataZeroVolumeRatioHigh>0.10`, `MarketDataFrozenRatioHigh>0.05`, `TooManyDeadFeatures>120`, `StrategyAucTooLow<0.52`, `PgIdleInTransactionTooLong>1800s`
  → **주목**: `StrategyAucTooLow` 임계가 이미 0.52 다. 이는 운영 범위가 0.52 근처임을 시스템 자신이 인코딩한 것으로, "0.6 목표"가 실체 없다는 오늘 결론과 일치한다.

### 7.2 모델 엔지니어 = **시점정합 + 누수 차단 + 학습·설계**
- 소유: `services/xgboost-ml/**`(feature_engine 포함), `scripts/wf_wave.py`, `scripts/champion_*.py`, `scripts/full_pipeline_dd.sh`
- 임무: 리서처가 넘긴 데이터를 **정제**하는 것보다 **as-of 정합·누수 차단·검증 프로토콜**을 지키는 것이 실질 업무다. 그 위에서 라벨·피처·앙상블을 설계·학습한다.
- 하드 게이트: 사전등록 문턱 · 확장창 다중폴드×다중시드 · 폴드별 짝비교 · 단일피처 AUC>0.75 또는 종목상수 피처 발견 시 중단 · 승격은 `--dry-run` 통과 후

### 7.3 트레이더 = **모델 산출 종목 → 전략 알고리즘 매매** (실주문은 trader-agent 소관)
- 소유: `scripts/feed_export.py`, `feed_server.py`, `data/feed/**`, `services/strategy-agents/**`, `services/trade-executor/**`, `docs/TRADER_INTEGRATION.md`
- 임무: ① 피드 계약(필드·필수값·신선도·전략별 청산규칙) 준수 ② 전략 파라미터 구성 ③ 실행 안전(페이퍼/실거래 분리, 한도, 킬스위치)
- **경계 명시**: 실제 주문 집행은 외부 `trader-agent` 가 한다. 우리 하네스의 트레이더 역할 권한은 **피드·전략설정·실행안전 게이트까지**이고, 실주문은 별도 승인 후에만.

### 7.4 핸드오프 계약 (하네싱의 핵심 — 산문 승인 금지, 파일 증거 필수)
1. **리서처 → 모델엔지니어**: 데이터 품질 성적표(`null_rate`, `asof_violation`, `stock_constant_ratio`, `padding_row_ratio`, 커버리지, 단위) — 통과 못 하면 피처로 제안 금지
2. **모델엔지니어 → 트레이더**: 검증 성적표(폴드 평균 AUC·std·폴드 승률, purge 적용 여부, 승격 dry-run 결과)
3. **트레이더 → 리서처**: 실현 성과 피드백(체결·슬리피지·전략별 손익) → 어떤 데이터가 실제로 기여했는지 환류

### 7.5 역할별 파일 소유권 (충돌 방지 — 2026-09-24 실사고 근거)
형제 에이전트 2개가 `supply_collector.py` 를 동시에 써서 한쪽 작업이 소실됐다. → 역할 간 **디렉터리 단위로 소유권을 겹치지 않게** 배정하고, 공유 파일(`feature_pipeline.py`, `market_features.py`, `company_features.py`) 수정은 **모델 엔지니어 단독 권한**으로 두며, 쓰기 전 mtime 확인을 규칙화한다.
