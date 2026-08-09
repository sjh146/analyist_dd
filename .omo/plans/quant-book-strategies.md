# quant-book-strategies - Work Plan

> **Version: v2 (2026-08-09, Hermes 심층 리뷰 반영)**
> v1: Prometheus - Plan Builder 작성 (12 todos / 6 waves + Metis 실측 갭분석 8건 반영). v2 변경:
> 1. **심층 리뷰(Hermes gate): P0/P1 없음** — 계획 승인.
> 2. **P2-1 명시**: PER/PBR 백테스트 시 `market_cap`이 현재가 기준(과거 시점 시총 미수집) → **경미한 look-ahead**가 존재. 심층분석 §2의 "market_cap 누락 종목 제외"에 더해, 백테스트 결과 해석 시 이 편향을 명시하고, 과거 시총 수집은 후속 이슈로 기록.
> 3. **P2-2 실행 전제 확인**: (a) 시장 지수 대용 `000001`(KRX KOSPI)이 yfinance/krx 수집기 데이터에 실제 존재하는지 T8 구현 전 확인 (없으면 유니버스 평균 폴백 — 계획서 §2 이미 명시) (b) `services/strategy-agents` 호스트 pytest 전역 설치 확인됨.
> 산출물: 계획서 + 심층분석 7섹션. 작성 노트: `.omo/drafts/quant-book-strategies.md`

## TL;DR (For humans)
<div style="display:none"><!-- TL;DR filled last below -- after plan body written. --></div>
**What you'll get:** 강환국『하면 된다! 퀀트투자』의 대표 투자 전략(가치·퀄리티·모멘텀·저변동성·멀티팩터)을 이 시스템에서 실제로 돌릴 수 있는 **팩터 계산 규칙 → 종목 선정 → 포트폴리오 → 매매 실행** 4단계 명세로 변환하고, 그 명세를 코드로 구현·백테스트·테스트까지 한 계획입니다. 완료되면 기존 3개 전략(테마/사이클/쌍둥이)은 그대로 둔 채 신규 '퀀트 팩터' 전략 3~4개가 페이퍼 트레이딩으로 작동합니다.

**Why this approach:** 책의 전략은 '미리 백테스트로 검증된 규칙'이므로 임계값과 결합 방식을 그대로 따르고(과최적화 금지), 이 시스템의 데이터 계층(financial_statements + market_data)에 구현 가능한 팩터만 선별했습니다. 핵심 결정은 (1) 재무 팩터(PER/PBR/ROE)는 '분기 재무만 읽는 공용 팩터 모듈 + point-in-time 규칙'으로 계산해 과적합·선견(look-ahead) 편향을 막고, (2) 실매매는 절대 켜지 않고 페이퍼/백테스트만 활성화한다는 것입니다.

**What it will NOT do:** 기존 Theme/Cycle/Twin 전략이나 Redis→Creon 실매매 파이프라인을 고치지 않습니다. 실매매 활성화 기능은 만들지 않습니다(이번 범위 밖). 책/방법론 텍스트를 그대로 복사하지 않고, 사실·규칙만 명세로 표현합니다. 판단 근거 없이 팩터 임계값을 파라미터로 '꼬아' 최적화하지 않습니다.

**Effort:** Large
**Risk:** Medium — 데이터 갭(PER/PBR/ROE 계산 경로, GP/A·PCR용 재무 필드 부재)과 포인트인타임(공시 지연) 편향이 주요 리스크.

**Decisions to sanity-check:** 시총 500억 KRW / 거래대금 10억 유니버스 하한, 재무 팩터 '분기 리밸런싱+모멘텀 월간' 분리, 베타/시장 대용으로 005930 대신 별도 KOSPI 지수 기반 수정, Z-score 동일가중 멀티팩터.

Your next move: **계획서 완성본으로 `${INSTRUCTIONS}` 지시에 따라 생성 완료 상태입니다.** 승인 후 실행(워커)이 진행됩니다. Full execution detail follows below.

---

> TL;DR (machine): Large | Medium | add FactorHub (4 factor strategies: Value/Quality/Momentum/MultiFactor), extend DART collector (gross_profit+cash_flow+per/pbr/roe path), point-in-time factor backtest via backtrader_integration, pytest unit+smoke tests. Additive non-destructive to existing 3 strategies & live pipeline; real-trading off.

## Scope
### Must have
- **매매규칙 명세 (책 → 4-레이어 규칙):**
  - 팩터 정의: 4대 팩터 ①가치(PER·PBR·PSR, PER=PBR 동일비중 결합) ②퀄리티(ROE 2년 평균, GP/A, 이익안정성) ③모멘텀(12-1, 3-6, 52주 고가 근접) ④저변동성(252일 연표준편차, 베타, long-only 랭킹용) — 각각 **데이터 소스 필드, 계산 기간, 정의 수식, 임계값** 명시.
  - 종목 선정: 유니버스 필터(시총 ≥ 500억 KRW, 최근 30일 평균 거래대금 ≥ 10억, 재무 데이터 ≥ 1개 분기, 상장 1년 이상) + 상위 N(가치/퀄리티/모멘텀 단독 = 상위 30, 멀티팩터 = 상위 20~30).
  - 포트폴리오: 동일가중, 재무 기반 전략 분기(3·6·9·12월 말) 리밸런싱, 모멘텀/저변동성 · 멀티팩터 월간 리밸런싱.
  - 매매 실행: 진입(전략 시그널 buy/sell dict), 청산(리밸런싱 시 매도 + StopLoss 7% / TP 15% 기본), 리스크(position sizer 연동). 실매매는 비활성.
- **데이터 갭 해소(비파괴):**
  - `financial_collector.py`에 DART `ifrs-full_GrossProfit`(GP/A용)와 영업현금흐름(PCR용) 수집 추가.
  - `per/pbr/roe` 계산 경로: financial_statements의 net_income/total_equity + stocks.market_cap으로 계산 (스키마 컬럼은 그대로 두고 팩터 모듈에서 계산).
- **구현(additive):**
  - 공용 팩터 모듈 `services/strategy-agents/app/factors/` (factor 계산 + point-in-time 재무 스냅샷 + 유니버스 필터).
  - **storage 읽기 메서드 확장(필수 선행)**: `postgres_storage.py`에 `get_financial_statements(stock_code, asof_date)`(report_date ≤ asof, point-in-time), `get_latest_financials`, `market_cap`·`trading_value` 조회(현재 `get_all_stocks`는 market_cap 미반환 — `postgres_storage.py:56`), `get_price_series(days)`로 12-1/3-6/52주 계산용 수익률 시리즈 조회. **현재 코드에 재무 조회 메서드가 전혀 없음**(코드그래프/파일 실측).
  - 전략 `ValueStrategy`, `QualityStrategy`, `MomentumStrategy`, `MultiFactorStrategy` (BaseStrategy 상속, `analyze()` → 시그널 dict). **전 팩터 전략 long-only**(sell 시그널은 long 포지션 종료만, short 금지).
  - **스키마 마이그레이션(비파괴)**: `financial_statements`에 `gross_profit`, `operating_cash_flow` 컬럼 추가(`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`), 신규 수집 메트릭 저장용.
  - `config/strategies/strategies.yaml`에 신규 전략 파라미터 + `strategy_config` DB 활성화 레코드(임계값은 코드 상수로 하드코딩, DB는 활성화 플래그만).
  - 펙터 백테스트: `backtrader_integration.py` 기반 재무/모멘텀 **포인트인타임 팩터 포트폴리오 백테스트** — 단, `PGDataFeed`의 기존 컬럼명 버그(`open/high/low/close`) 수정과 `FactorPortfolioStrategy`(랭크 기반 리밸런싱) 신규 클래스 포함(아래 심층분석 §7).
- **테스트:** 팩터 계산 단위테스트(고정 fixture), 규칙 단위테스트(임계값·상위N), 전략 스모크 테스트, 백테스트 스모크(소규모), 기존 전략 회귀 테스트.
- **심층분석(문서):** 분석 이슈·데이터 갭·한국 시장 특성·멀티팩터 방법론·리밸런싱 실전 이슈·과최적화 방지를 계획서 하단 `## 참고: 심층분석`에 기술.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **기존 전략(Theme/Cycle/Twin)·실행 파이프라인 수정 금지.** `main.py`의 기존 루프 로직 변경 없이 신규 전략은 additive 등록. `theme_strategy.py`/`cycle_strategy.py`/`twin_strategy.py`/`trade-executor`/`api-gateway` 코드 변경 금지.
- **실매매 활성화 금지(이번 범위).** 신규 전략은 페이퍼/백테스트만. Redis `trade:signals`로의 실발행은 실매매로 간주 → 신규 전략 시그널은 페이퍼 큐/백테스트로 제한.
- **과최적화 금지.** 책의 규칙 임계값(상위 30/동일가중/12-1·3-6 모멘텀/Z-score 결합)을 임의 변경 금지. 변경은 명시적 한국 시장 근거만.
- **저작권 침해 금지.** 책 텍스트·표 절대 복사 금지. 전략은 '사실·방법론·규칙 명세'로만 표현.
- **데이터 수집기 비파괴.** `financial_collector.py`는 기존 수집 결과/필드를 유지하고 신규 메트릭을 추가/계산만. 스키마 변경(새 컬럼) 필요 시 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`로 기존 동작 보존.
- **시크릿 커밋 금지.** `.env`/API 키(파일 내 하드코딩 포함) 커밋 금지.
- **심각한 신규 아키텍처 도입 금지.** 신규 DB 테이블/큐/외부 의존성 추가는 불필요 시 금지(가능하면 기존 테이블+파일 기반).

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **TDD** (팩터/규칙 먼저 테스트, 구현은 이를 만족하도록) + framework = `pytest 9.1.1`
- 실행 환경(실측): pytest 전역 설치됨. strategy-agents 테스트는 `services/strategy-agents/` 디렉터리에서 실행(테스트가 `from app.*` import — 기존 `services/strategy-agents/tests/test_position_sizer_ext.py` 패턴). Docker 내부: `docker compose run --rm strategy-agents python -m pytest tests/ -v --tb=short` (scripts/run_tests_in_docker.sh 패턴).
- Evidence: `.omo/evidence/task-<N>-quant-book-strategies.json` (각 todo에 고정 fixture JSON + 실행 결과 JSON 저장)
- 각 todo의 Acceptance는 아래 명령(exact command)로 agent가 직접 실행해 통과 판정.

## Execution strategy
### Parallel execution waves
- **Wave 1 — 데이터 기반(팩터 모듈 진입점):** 팩터 공용 모듈 뼈대 + point-in-time 재무 스냅샷 + 유니버스 필터 (T1, T2). 단위테스트 포함.
- **Wave 2 — 데이터 갭 해소:** DART 재무 수집 확장(gross_profit, operating_cash_flow) + per/pbr/roe 계산 경로 (T3, T4). T1의 재무 스냅샷을 채울 수 있게.
- **Wave 3 — 단독 팩터 전략:** ValueStrategy (T5), QualityStrategy (T6), MomentumStrategy (T7), LowVolatilityStrategy (T8) — 각각 단위+규칙 테스트.
- **Wave 4 — 결합 전략:** MultiFactorStrategy (Z-score 동일가중 top 20~30) (T9). Wave 3의 단독 팩터 팩터 점수를 재사용.
- **Wave 5 — 통합·백테스트·실행 연동:** 전략 등록(config YAML + DB 활성화 + main.py additive) (T10), 팩터 백테스트 스크립트 (T11), 회귀/스모크 (T12).
- **Wave 6 — 최종 검증:** F1~F4 병렬 파이널 검증 웨이브.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 factor-module | — | T5,T6,T7,T8,T9 | T2,T3 |
| T2 universe-filter | — | T5,T6,T7,T8,T9 | T1,T4 |
| T3 dart-collector-ext | — | T4 | T1,T2 |
| T4 per-pbr-roe-path | T3 | T5,T6 | T2 |
| T5 value-strategy | T1,T2,T4 | T9 | T6,T7,T8 |
| T6 quality-strategy | T1,T2,T4(T3일부) | T9 | T5,T7,T8 |
| T7 momentum-strategy | T1,T2 | T9 | T5,T6,T8 |
| T8 lowvol-strategy | T1,T2 | T9 | T5,T6,T7 |
| T9 multifactor-strategy | T5,T6,T7,T8,T1,T2 | T10 | — |
| T10 registration | T9 | T11,T12 | — |
| T11 backtest | T10 | T12 | — |
| T12 regression-smoke | T10,T11 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo.
> 공통 실행 코드(각 todo에서 사용): 
> - 단위테스트: `cd /home/dduckbeagy/analyist_dd/services/strategy-agents && python -m pytest tests/test_<name>.py -v --tb=short`
> - Docker 내부(전체): `cd /home/dduckbeagy/analyist_dd && docker compose run --rm strategy-agents python -m pytest tests/ -v --tb=short`

- [x] 1. **공용 팩터 모듈 뼈대 + storage 읽기 메서드 확장 + point-in-time 재무 스냅샷**
  What to do: `services/strategy-agents/app/factors/` 디렉터리 생성. `__init__.py`(공개 API 명세), `factor_base.py`(추상 기반: `compute(stocks, asof_date) -> List[Dict]`, factor score 정규화 메서드), `financial_snapshot.py`(point-in-time 재무 스냅샷). **먼저 `services/strategy-agents/app/storage/postgres_storage.py`에 신규 읽기 메서드를 추가(비파괴, 기존 메서드 보존)**: ① `get_financial_statements(stock_code, asof_date)` — `SELECT ... WHERE report_date <= %s` 최신 1건/모든 행(point-in-time) ② `get_latest_financials(stock_code)` ③ `get_market_caps()` — `stocks.market_cap` 반환(현재 `get_all_stocks`는 market_cap 미반환, `postgres_storage.py:56`) ④ `get_avg_trading_value(stock_code, days=30)` — `market_data.trading_value` 평균 ⑤ `get_price_series`는 이미 존재하나 12-1/3-6/52주용으로 `days=270` 이상 + 원시 close 반환 확인. `financial_snapshot.py`는 이 storage 메서드를 호출(직접 psycopg2 조회 금지 — 기존 pool 재사용). asof_date로 '미래 보고서 제외' 보장. Must NOT: 기존 storage 메서드 삭제/시그니처 변경(추가만).
  Parallelization: Wave 1 | Blocked by: — | Blocks: T5,T6,T7,T8,T9
  References: `services/strategy-agents/app/strategies/base_strategy.py:13-40`(기반 패턴), `init-scripts/postgres/01_schema.sql:160-175`(financial_statements 컬럼), `services/strategy-agents/app/storage/postgres_storage.py:49-110`(기존 DB 조회 패턴 — psycopg2 pool), `scripts/run_tests_in_docker.sh:7`(테스트 실행 패턴)
  Acceptance criteria: `cd /home/dduckbeagy/analyist_dd/services/strategy-agents && python -m pytest tests/test_factor_base.py tests/test_financial_snapshot.py -v --tb=short` → 0 failed. 해당 테스트 파일은 아래 QA가 작성.
  QA scenarios: happy — 고정 fixture(재무 행 3분기)에서 asof_date보다 새 report_date 행을 제외하고 최신 1건 반환 assert. failure — asof_date가 모든 report_date보다 이전이면 빈 결과(에러 아닌 빈 리스트) assert. Evidence: `.omo/evidence/task-1-quant-book-strategies.json`
  Commit: Y | `feat(factors): add factor base module and point-in-time financial snapshot`

- [x] 2. **유니버스 필터 (시총/거래대금/재무/상장기간)**
  What to do: `services/strategy-agents/app/factors/universe.py` 추가. 함수 `filter_universe(stocks, asof_date, min_market_cap=5e10, min_avg_trading_value=1e9, ...) -> List[str]`. 기준: `stocks.market_cap`(T1의 storage 메서드로 조회) ≥ 500억 KRW(5e10), 최근 30일 평균 거래대금(Market data `trading_value`) ≥ 10억(1e9), point-in-time 재무 데이터 ≥ 1개 분기, 상장 1년 이상(market_data 최초 행이 asof_date-252일 이전). 시가총액 NULL 종목 제외. Must NOT: 임계값 하드코딩하되 함수 파라미터로 오버로딩 가능(과최적화 경고 주석 포함). `get_all_stocks`가 market_cap을 최근 추가로 반환하면 그 값을 사용(기존 호출 영향 없이 add만).
  Parallelization: Wave 1 | Blocked by: — | Blocks: T5,T6,T7,T8,T9
  References: `init-scripts/postgres/01_schema.sql:9-33`(stocks.market_cap, market_data.trading_value), `services/strategy-agents/app/storage/postgres_storage.py:49`(stocks 조회)
  Acceptance criteria: 단위테스트 통과(임계값 경계 케이스). 
  QA: happy — mock stocks dict에서 시총 미달 종목 제외, 거래대금 미달 제외, 충족 종목만 남는지 assert. failure — market_cap NULL 종목이 조용히 제외(에러 미발생) assert. Evidence: `.omo/evidence/task-2-quant-book-strategies.json`
  Commit: Y | `feat(factors): add universe filter (market cap, trading value, listing age)`

- [x] 3. **DART 재무 수집 확장 (gross_profit, operating_cash_flow)**
  What to do: `services/yfinance-collector/app/collectors/financial_collector.py`의 `FINANCIAL_METRICS_MAP`(L28-35)에 `"ifrs-full_GrossProfit": "gross_profit"`, `"ifrs-full_CashFlowsFromUsedInOperatingActivities": "operating_cash_flow"` 추가. **스키마 마이그레이션(비파괴)**: `financial_statements`에 `gross_profit DECIMAL(30,4)`, `operating_cash_flow DECIMAL(30,4)` 컬럼이 없으므로 `init-scripts/postgres/01_schema.sql`에 `ALTER TABLE financial_statements ADD COLUMN IF NOT EXISTS ...` 추가(멱등). **수집 히스토리 보존**: `aggregate_to_financials`(L153-171)는 가장 최근 report_date만 유지하는데, point-in-time 백테스트를 위해 **모든 report_date 행을 유지**하도록 확인 — 스키마 `UNIQUE(stock_code, report_date)`(01_schema.sql:174)가 지원하므로 수집기가 분기별 행을 누적 저장하는지 검증하고, 최신만 덮어쓰는 경우 과거 히스토리 저장 경로를 추가. Must NOT: 기존 6개 매핑·revenue/operating_profit/net_income 총자산 수집 로직 변경(추가만). 저장 메서드가 부분 업데이트로 비파괴 확인.
  Parallelization: Wave 2 | Blocked by: — | Blocks: T4
  References: `services/yfinance-collector/app/collectors/financial_collector.py:28-35,78-171`, `init-scripts/postgres/01_schema.sql:160-175`, `services/yfinance-collector/app/storage/postgres_storage.py`(저장 메서드 확인해 사용)
  Acceptance criteria: `cd /home/dduckbeagy/analyist_dd/services/yfinance-collector && python -m pytest tests/test_financial_collector_ext.py -v --tb=short` 통과. (테스트는 DART 대신 mock 응답으로 GrossProfit/OperatingFlow 매핑 assert.)
  QA: happy — mock DART JSON에 `ifrs-full_GrossProfit` 값 넣고 aggregator가 gross_profit 키로 저장 assert. failure — DART 키 누락 시 key error 대신 해당 필드 미포함(안전) assert. Evidence: `.omo/evidence/task-3-quant-book-strategies.json`
  Commit: Y | `feat(yfinance-collector): collect gross_profit and operating cash flow from DART`

- [x] 4. **PER/PBR/ROE 계산 경로 (팩터 모듈)**
  What to do: `services/strategy-agents/app/factors/value_ratios.py` 추가. 함수 `compute_ratios(snapshot, market_cap_map) -> Dict[code, {per, pbr, roe, psr, gpa}]`: PE = market_cap / net_income, PB = market_cap / total_equity, ROE = net_income / total_equity, PSR = market_cap / revenue, GPA = gross_profit / total_assets(유/무 조건부). market_cap은 `stocks.market_cap`. 음수/0 net_income·equity는 해당 비율 None 처리(과최적화 아닌 방어적 처리). Must NOT: financial_statements 내 per/pbr/roe 스키마 컬럼에 직접 기록하지 않고 **계산 결과만 반환**(스키마 컬럼은 후속 작업 시 채울 수 있으나 이번엔 계산 경로만으로 충분). 
  Parallelization: Wave 2 | Blocked by: T3 | Blocks: T5,T6
  References: `init-scripts/postgres/01_schema.sql:9-19,160-175`, `services/yfinance-collector/app/collectors/financial_collector.py:28-35`(추가된 gross_profit/operating_cash_flow)
  Acceptance criteria: 단위테스트로 고정 재무+시총에서 계산값 assert (eps/equity 기반 not zero-divide).
  QA: happy — 1e12 net_income/1e13 market_cap → PER 10.0 assert. failure — net_income=0 → per=None (ZeroDivision 아님) assert. Evidence: `.omo/evidence/task-4-quant-book-strategies.json`
  Commit: Y | `feat(factors): compute PER/PBR/ROE/PSR/GPA from financial snapshot`

- [x] 5. **ValueStrategy (가치 전략)**
  What to do: `services/strategy-agents/app/strategies/value_strategy.py` 추가. `super().__init__("value_factor", storage)`. 규칙: 유니버스(T2) 내 종목에 대해 ① 낮은 PER ② 낮은 PBR ③ 낮은 PSR 각각 크로스섹션에서 '낮을수록 좋은' 값 → 두 결합 방식 제공: `mode="per_pbr"`(PER와 PBR 각각 상위(낮은 쪽) 랭크 → 동일비중으로 평균 랭크 → 상위 30종목), `mode="psr"`(낮은 PSR 상위 30 종목, 대안). `analyze()`: asof_date=오늘, point-in-time snapshot + 최신 market_cap으로 value_ratios 계산 → 상위 랭크 30종목 buy, 보유 중 리밸런싱/하락 종목 sell. 시그널 dict: action/stock_code/price=0/reason=strategy_name="value_factor"/confidence(랭크 정규화 0.5~0.95). 사용자 관례: 분기 리밸런싱(3·6·9·12월 말). Must NOT: 기존 전략 파일 수정. t-1 대용으로 다음 리밸런싱일 판단은 config `rebalance_interval_days`(기본 63) 사용.
  Parallelization: Wave 3 | Blocked by: T1,T2,T4 | Blocks: T9
  References: `services/strategy-agents/app/strategies/theme_strategy.py:14-71`(analyze→signals 패턴), `base_strategy.py:13-40`, T1/T2/T4의 factors 모듈
  Acceptance criteria: `python -m pytest tests/test_value_strategy.py -v --tb=short` 통과 (mock storage로 상위 30 내 buy 신호, 하위 30 sell).
  QA: happy — 40종목 fake 중 상위 30 buy, 나머지 sell assert. failure — 재무 데이터 없는 종목 제외(신호 없음, 예외 없음) assert. Evidence: `.omo/evidence/task-5-quant-book-strategies.json`
  Commit: Y | `feat(strategies): ValueStrategy (low PER/PBR/PSR, top-30)`

- [x] 6. **QualityStrategy (퀄리티 전략)**
  What to do: `services/strategy-agents/app/strategies/quality_strategy.py` 추가. `super().__init__("quality_factor", storage)`. 규칙: ① ROE 2년 평균(최근 2개 분기 point-in-time ROE 평균, 높을수록) ② GP/A(매출총이익/총자산 — T4에서 gross_profit None이면 제외) ③ 이익 안정성(최근 2년 순이익 증가 — 연간 net_income 비교). 세 팩터 각각 크로스섹션 랭크 → 동일비중 평균 랭크 → 상위 30 구매/이탈 매도. 시그널 dict 패턴 동일. Must NOT: 기존 전략 수정. GP/A 데이터 없으면 해당 팩터 비중 0으로(None 허용) 전체 결합 안전.
  Parallelization: Wave 3 | Blocked by: T1,T2,T4 | Blocks: T9
  References: `init-scripts/postgres/01_schema.sql:160-175`, T4 value_ratios, theme_strategy.py:14-71
  Acceptance criteria: `python -m pytest tests/test_quality_strategy.py -v --tb=short` 통과 (2개 분기 ROE 평균·GP/A·이익안정성 정렬 assert).
  QA: happy — ROE 높은 2종목 상위 랭크 assert. failure — gross_profit None 종목은 GP/A 무시하고 나머지 팩터로만 랭크(에러 없음) assert. Evidence: `.omo/evidence/task-6-quant-book-strategies.json`
  Commit: Y | `feat(strategies): QualityStrategy (ROE avg, GP/A, earnings stability)`

- [x] 7. **MomentumStrategy (모멘텀 전략)**
  What to do: `services/strategy-agents/app/strategies/momentum_strategy.py` 추가. `super().__init__("momentum_factor", storage)`. 규칙: `market_data.close_price` 기준 ① 12-1 모멘텀 = `(P_t / P_{t-252}) - (P_t / P_{t-21})` (252=1년, 21=1개월 거래일 근사) ② 3-6 모멘텀 = `(P_t / P_{t-63}) - (P_t / P_{t-126})` ③ 52주 고가 근접 = `P_t / MAX(P_{t-252..t})`. 세 팩터 크로스섹션 랭크 → 동일비중 평균 → 상위 30 구매/이탈 매도. **long-only**: 책의 모멘텀 전략은 하락 종목 매도를 원하지만 한국 공매도 제한으로 **short 금지, sell은 long 종료만**. 상장 1년 미만(가격 이력 부족) 종목은 제외(T2). 신규 저장 메서드(T1)의 가격 시리즈로 계산(기존 `get_latest_momentum`은 20일로 서로 다른 horizon — **미사용**, 12-1/3-6/52주용 시리즈 계산 메서드 사용). 시그널 dict 패턴 동일. Must NOT: 기존 전략 수정. 12-1에서 최근 1개월 재사용 금지 규칙 준수(차감 연산 정확히 구현).
  Parallelization: Wave 3 | Blocked by: T1,T2 | Blocks: T9
  References: `init-scripts/postgres/01_schema.sql:22-33`(market_data), `services/strategy-agents/app/storage/postgres_storage.py:88-110,315-336`(가격 시리즈 조회)
  Acceptance criteria: `python -m pytest tests/test_momentum_strategy.py -v --tb=short` 통과 (고정 가격 시리즈로 12-1/3-6/52주 계산 정확히 assert).
  QA: happy — 4종목 가격 시리즈로 모멘텀 순위 상위 30 중 2종목 buy assert. failure — 가격 이력 252일 미만 종목 제외 assert. Evidence: `.omo/evidence/task-7-quant-book-strategies.json`
  Commit: Y | `feat(strategies): MomentumStrategy (12-1, 3-6, 52-week proximity)`

- [x] 8. **LowVolatilityStrategy (저변동성 전략)**
  What to do: `services/strategy-agents/app/strategies/lowvol_strategy.py` 추가. `super().__init__("lowvol_factor", storage)`. 규칙: ① 252일 연표준편차(일별 로그수익률 std × √252) 낮을수록 ② 베타 = `cov(개별수익률, 시장수익률)/var(시장수익률)` 낮을수록(long-only 구매용. 공매도 금지 — 음수 베타 종목 제외). 시장 대용: `stocks` 마스터의 별도 지수 종목(코드 config `market_index_code` 기본 `"000001"` KRX KOSPI)이 있으면 사용, 없으면 전체 유니버스 평균 수익률을 시장 대용. 두 팩터 랭크 동일비중 → 상위 30(저변동 종목) 구매/이탈 매도. ✓ 이 전략은 '한국 공매도 제한'에 대응해 **long-only 랭킹 전용**(매도 신호는 진입 전 보유분 정리만). Must NOT: 공매도(short) 신호 생성 금지 — 시그널 action은 buy/sell(long 포지션 종료)만.
  Parallelization: Wave 3 | Blocked by: T1,T2 | Blocks: T9
  References: `init-scripts/postgres/01_schema.sql:22-33`, README.md '삼성전자를 시장 지수로 사용'이슈(#6, T8에서 지수 대용 문제 해결 대상), theme_strategy.py 패턴
  Acceptance criteria: `python -m pytest tests/test_lowvol_strategy.py -v --tb=short` 통과 (고정 수익률 시리즈로 변동성/베타 assert).
  QA: happy — 저변동 2종목 상위 랭크 buy assert. failure — 시가총액·시장 대용 수익률 무(空)여도 종목 제외로 에러 없이 안전 assert. Evidence: `.omo/evidence/task-8-quant-book-strategies.json`
  Commit: Y | `feat(strategies): LowVolStrategy (low 252d vol + beta, long-only)`

- [x] 9. **MultiFactorStrategy (멀티팩터 결합)**
  What to do: `services/strategy-agents/app/strategies/multifactor_strategy.py` 추가. `super().__init__("multifactor", storage)`. 규칙: 가치(T5)·퀄리티(T6)·모멘텀(T7)·저변동(T8) 각 단독 팩터의 **크로스섹션 Z-score**((x-μ)/σ, 시그너 방향 통일: 낮은 PER/PBR=양(+) 등록)을 산출해 **동일가중 합산** → 합산 점수 상위 20~30종목(기본 20). 포트폴리오 동일가중, 월간 리밸런싱. 재사용: T5~T8의 팩터 계산 로직을 호출(중복 코드 금지 — factors 모듈에서 공용 함수). 시그널 dict 패턴 동일, confidence=합산 Z-score 정규화. Must NOT: 기존 전략 수정. Z-score에 |σ| < 1e-8이면 해당 팩터 해당 종목 제외 방어. 과최적화 방지: 팩터 가중치는 책의 '동일가중' 그대로 — 가중치 튜닝 금지.
  Parallelization: Wave 4 | Blocked by: T5,T6,T7,T8 | Blocks: T10
  References: `services/strategy-agents/app/strategies/*_strategy.py`(T5-T8), factors 모듈, theme_strategy.py 패턴
  Acceptance criteria: `python -m pytest tests/test_multifactor_strategy.py -v --tb=short` 통과 (고정 팩터 Z-score combo로 상위 20 select assert).
  QA: happy — 30종목 합산 Z-score에서 상위 20 buy assert. failure — 한 팩터 Z-score 분산 0이어도 나머지로 정상 랭크 assert. Evidence: `.omo/evidence/task-9-quant-book-strategies.json`
  Commit: Y | `feat(strategies): MultiFactorStrategy (equal-weight Z-score combo, top-20)`

- [x] 10. **전략 등록·활성화 (config YAML + strategy_config DB + main.py additive)**
  What to do: ① `config/strategies/strategies.yaml`에 신규 4개+저변동(모두 `is_active` 플래그) 이름/description/parameters 추가 → `strategy_config` 테이블(JSONB)에 활성화 레코드 upsert(**임계값은 코드 상수로 하드코딩하고 DB 파라미터엔 비활성화만 — 과최적화 방지**). ② `main.py`의 `__init__`에 신규 전략 인스턴스 추가, `run_all_strategies()`에 각 `analyze()` try/except 블록 추가(**기존 3개 전략 블록 코드 수정 금지 — 신규 try 절만 append**). → **Metis 지적 반영: `main.py`는 의도적으로 수정 대상임**(전략 등록 지점)을 명시. ③ **실매매 방지 게이트**: 신규 팩터 전략 시그널이 `trade:signals`(실매매) 그룹에 발행되지 않도록, 팩터 전략은 별도 `paper_only` 채널(예: `paper:factor_signals`)로 발행하고 `_process_and_publish`를 통한 실발행 경로에 진입 금지. 실제 실발행은 `strategy_config.is_active=true` + `paper_only=false` 명시적 변경 시에만(이번 범위는 `paper_only=true` 고정).
  Parallelization: Wave 5 | Blocked by: 9 | Blocks: 11,12
  References: `services/strategy-agents/app/main.py:42-45,50-108,110-140`, `services/strategy-agents/app/storage/redis_storage.py:54`(publish_signal), `config/strategies/strategies.yaml`(전체), `init-scripts/postgres/01_schema.sql:128-136`(strategy_config)
  Acceptance criteria: `docker compose run --rm strategy-agents python -m pytest tests/test_registration.py -v --tb=short` 통과 + `docker compose run --rm strategy-agents python -m app.main`이 기존 3전략 런타임 에러를 **그대로 두고**(비파괴) 신규 4전략 신호를 페이퍼로 생성(에러 없이 로그).
  QA: happy — main 실행 후 신규 전략 시그널 로그 확인(Redis 페이퍼 큐). failure — 기존 전략 예외 발생해도 try/except로 신규 전략 블록은 계속 실행(전체 프로세스 안 죽음) assert. Evidence: `.omo/evidence/task-10-quant-book-strategies.json`
  Commit: Y | `feat(config): register factor strategies (value/quality/momentum/lowvol/multifactor), paper-only`

- [ ] 11. **팩터 포인트인타임 백테스트 스크립트 (backtrader 기반, PGDataFeed 버그 수정 포함)**
  What to do: ① **선행 필수 수정(기존 버그)**: `services/backtester/backtrader_integration.py:42-43`의 `PGDataFeed._fetch_from_db`가 `SELECT trade_date, open, high, low, close, volume`을 조회하지만 실제 스키마 컬럼은 `open_price/high_price/low_price/close_price`(`init-scripts/postgres/01_schema.sql:26-30`). **컬럼명을 실스키마에 맞게 수정**하고 DB 연동 테스트 추가(기존 테스트는 synthetic 경로만 커버해 이 버그를 놓침). ② **팩터 포트폴리오 전략 클래스 추가**: 기존 `StrategyWrapper`(단일 종목 신호 재생, `backtrader_integration.py:99-116`)는 랭크 기반 팩터 포트폴리오(상위 N·동일가중·주기 리밸런싱)를 표현 불가 → 신규 `FactorPortfolioStrategy`(날짜별 랭킹 유니버스 입력, 리밸런싱 시점 상위 N 매수·이탈 매도, 동일가중) 추가. ③ `services/backtester/scripts/factor_backtest.py`(+README) 추가: 유니버스(T2) + 팩터(T4~T8)로 리밸런싱일마다 상위 N 선택 → `FactorPortfolioStrategy` 신호 구성 → `create_cerebro`/`run_backtest` 실행. 파라미터: strategy/universe/start/end/top_n/rebalance. 출력: `.omo/evidence/factor-backtest-<strategy>.json`(total_return, sharpe_ratio, max_drawdown, win_rate, num_trades). 책의 주장과 비교 표 주석. Must NOT: `backtrader_integration.py`의 StrategyWrapper/create_cerebro/run_backtest 기존 동작 회귀(버그 수정 + 신규 클래스만 additive). 실매매 금지(순수 백테스트).
  Parallelization: Wave 5 | Blocked by: 10 | Blocks: 12
  References: `services/backtester/backtrader_integration.py:12-177`(PGDataFeed:40-51, StrategyWrapper:99-116, create_cerebro:119-141, run_backtest:144-152), `init-scripts/postgres/01_schema.sql:22-33`(실제 컬럼명), 2,4-8 팩터 모듈
  Acceptance criteria: `cd /home/dduckbeagy/analyist_dd/services/backtester && python scripts/factor_backtest.py --strategy value_factor --top-n 5 --start 2024-01-01 --end 2024-06-30`이 성공해 JSON 결과 파일 생성 (5종목 이하 소규모 스모크).
  QA: happy — 5종목 스모크 실행 시 num_trades>0, 결과 파일 생성 assert. failure — 유니버스 0개면 빈 결과(에러 아님) 반환 assert. Evidence: `.omo/evidence/task-11-quant-book-strategies.json`
  Commit: Y | `feat(backtester): point-in-time factor backtest script via backtrader`

- [ ] 12. **회귀 + 전체 스모크 테스트**
  What to do: **추가 검증 모듈(이 todo의 핵심)** ① **point-in-time 테스트**: `report_date`가 asof 이후인 재무 행은 팩터 계산/백테스트에서 제외 — `financial_snapshot` 단위테스트 + 백테스트 asof 경계 테스트 추가. ② **임계값-그대로(과최적화 방지) 테스트**: 각 팩터 전략의 임계 상수(PER/PBR/모멘텀 horizon/상위 N/동일가중)가 책 규칙 값과 일치하며 파라미터로 튜닝되지 않았는지 assert. ③ **long-only 테스트**: 4개 팩터 전략이 emit하는 신호에 `sell`(long 종료)만 존재하고 `short` 신호 0임을 assert. ④ **회귀 베이스라인**: 기존 Theme/Cycle/Twin `analyze()`의 신호 형태(구조)를 변경 전 스냅샷으로 찍고, 변경 후에도 동일한지 assert — 신규 작업이 기존 전략을 손대지 않았음을 자동 증명. ⑤ **no-secrets 체크**: `git diff`/작업 트리에 `DART_API_KEY=`/`POSTGRES_PASSWORD`/`.env` 키 포함 여부 grep으로 0건 assert. 그 후 기존 전략 회귀(기존 테스트) + 전체 스모크(services/strategy-agents, backtester, yfinance-collector 각 디렉터리). **기존 테스트는 수정 금지** — 신규 테스트만 추가. 검증만(구현 없음).
  Parallelization: Wave 5 | Blocked by: 10,11 | Blocks: F1,F2,F3,F4
  References: `services/strategy-agents/tests/test_position_sizer_ext.py`, `test_stop_loss_ext.py`, `test_portfolio_risk.py`, `services/backtester/tests/test_backtrader_integration.py`(기존 회귀 대상), 신규 팩터/전략 모듈
  Acceptance criteria: 아래 세 명령 각각 0 failed:
  1. `cd /home/dduckbeagy/analyist_dd/services/strategy-agents && python -m pytest tests/ -v --tb=short`
  2. `cd /home/dduckbeagy/analyist_dd/services/backtester && python -m pytest tests/ -v --tb=short`
  3. `cd /home/dduckbeagy/analyist_dd/services/yfinance-collector && python -m pytest tests/ -v --tb=short`(존재 시; 없으면 스킵 확인)
  QA: happy — 세 디렉터리 모두 0 failed. failure — 기존 전략 테스트 하나깨짐 = 회귀 버그 → 즉시 되돌리고 이 todo는 재검증. Evidence: `.omo/evidence/task-12-quant-book-strategies.json`
  Commit: Y | `test(strategies): regression + full smoke suite`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — 계획서 Must have(팩터 4대·데이터 갭·4레이어·비파괴·실매매off) 충족 및 Must NOT(기존 전략/실행/과최적화/커밋) 위반 여부 전수 검사. Evidence: `.omo/evidence/f1-plan-audit.json`
- [ ] F2. Code quality review — 신규 파일 250줄 이하, 기존 전략/git diff에 의도치 않은 변경 0, AST-grep으로 `action in ("buy","sell")` 유지·`short` 신호 0 검사. Evidence: `.omo/evidence/f2-code-review.json`
- [ ] F3. Real manual QA — `docker compose run --rm strategy-agents python -m app.main` 정상 실행(신규 페이퍼 신호 로그, 기존 전략 에러는 예외 블록 처리로 전체 안 죽음) + 백테스트 스모크 재실행. Evidence: `.omo/evidence/f3-manual-qa.json`
- [ ] F4. Scope fidelity — 구현 범위가 계획서(팩터+유니버스+4전략+백테스트+데이터확장)와 일치, 초과 구현(실매매/새 DB/새 큐) 없음. Evidence: `.omo/evidence/f4-scope.json`

## Commit strategy
- 신규 기능 파일은 한도 내 atomic 커밋: `feat(factors|strategies|yfinance-collector|backtester|config): <동사+객체>`.
- 테스트는 해당 기능과 함께(구현+테스트=1 todo). 회귀는 `test(...)`.
- 데이터 갭 스키마 변경은 `feat(yfinance-collector)` 또는 별도 `migration` 커밋으로 분리.
- 비밀 미포함: 커밋 전 `git status --short`로 `.env`/키 파일 제외 확인, `.gitignore`에 이미 등록 확인.
- 커밋 컨벤션: Conventional Commits (기존 git log 패턴 참고 — 하위 호환).
- 브랜치: 이 작업은 별도 브랜치 `feat/quant-book-strategies`에서 진행하고, main 머지 전 최종 검증 통과 필요.

## Success criteria
- [ ] 신규 4개 전략(가치/퀄리티/모멘텀/멀티팩터)이 `run_all_strategies()`에서 페이퍼 시그널을 생성(에러 無, is_active=true).
- [ ] 기존 Theme/Cycle/Twin·PositionSizer·StopLoss 동작 불변(회귀 테스트 통과).
- [ ] 데이터 갭 해소: DART에서 gross_profit·operating_cash_flow 수집, PER/PBR/ROE/PSR/GP/A가 point-in-time으로 계산 가능.
- [ ] 팩터 백테스트 스모크 실행 성공, 책의 전략 규칙을 그대로 채택(과최적화 미적용).
- [ ] `services/strategy-agents/tests/`와 `services/backtester/tests/`의 pytest 전부 통과.
- [ ] 실매매 비활성(paper/backtest만) 대조 코드 확인.

---

## 참고: 심층분석 (강환국『하면 된다! 퀀트투자』 → analyist_dd 규칙 변환)

> 분석 이슈·해석 결정·한국 시장 반영·방법론에 대한 서술. 코드가 아니라 '왜 이 규칙이 이렇게 되었는가'의 근거.

### 1) 책 전략 → 규칙 변환의 해석 이슈
- **책은 '팩터 랭크 → 상위 30 동일가중'이지만, 저장소가 집합(PER/PBR/ROE)과 유동 주기(모멘텀)가 다르다.** 책은 연 1회 또는 분기 재조정을 단일 유니버스에서 실행하지만, analyist_dd는 `financial_statements`(분기 report_date)와 `market_data`(일일)를 분리 저장한다. 따라서 4-레이어 명세에서 '재무 팩터 = 공시시점 기준(point-in-time) 스냅샷', '가격 팩터 = 리밸런싱 직전 날짜 기준'으로 분리해 시간 축을 맞춰야 한다. 이 변환의 핵심 해석 결정이며, 무시하면 선견(look-ahead) 편향이 생긴다.
- **책의 '논문 그대로' 임계값 채택**: 책은 '상위 30종목', '동일가중', '12-1 모멘텀', '3-6 모멘텀'을 백테스트 검증 기준으로 제시한다. 이 계획서는 이 임계값을 **하드코딩된 규칙**으로 두고(과최적화 방지) 한국 시장에서 '재검증'만 허용 — 파라미터 튜닝은 명시적 근거가 있을 때만.
- **'가치+퀄리티+모멘텀 조합'의 멀티팩터**: 책은 팩터 합산 방식(Z-score 또는 랭크 평균)을 '동일가중'으로 권장한다. 이 계획서는 **크로스섹션 Z-score를 동일가중 합산**해야 과최적화(가중치 튜닝)를 피한다.

### 2) 데이터 갭 분석 (구현 불가 → 수집 확장 판단)
- financial_statements 스키마(init-scripts/postgres/01_schema.sql:160-175)의 컬럼 `per/pbr/roe/debt_ratio`는 **존재하지만 DART 수집기(financial_collector.py:28-35)가 채우지 않는다.** → 이 계획서는 '스키마 컬럼에 쓰지 않고' 팩터 모듈에서 market_cap + net_income/total_equity로 계산하는 경로를 추가해, 컬럼 vs 수집 불일치 문제를 회피한다.
- **GP/A(**매출총이익/총자산**)에 필요한 gross_profit**: DART `ifrs-full_GrossProfit` 미수집 → T3에서 추가. 총자산은 이미 `ifrs-full_Assets`로 수집됨.
- **PCR에 필요한 영업현금흐름**: 미수집 → T3에서 `ifrs-full_CashFlowsFromUsedInOperatingActivities` 추가. (책의 PCR 전략은 여건상 후순위로 두고, 필수 우선 GP/A→멀티팩터.)
- **PER/PBR 결정 팩터의 '시가총액'**: `stocks.market_cap` 컬럼 존재하나 **최신성 유지가 미보장**(README 역사상 누락 가능). T4는 `market_cap`을 `현재가 × 발행주식수` 또는 마스터 최신값으로 위임 — 백테스트 포인트인타임 시 `market_cap` 누락 종목은 제외.
- **베타의 시장 지표**: README 알려진 이슈 #6 '삼성전자를 시장 지수로 사용' → 저변동 전략(T8)에서 시장 대용으로 `market_index_code`(기본 KRX KOSPI 000001) 또는 유니버스 평균을 사용. 005930 단일 종목을 시장 대용으로 쓰는 기존 버그를 답습하지 않는다.

### 3) 한국 시장 특성 반영
- **공매도 제한**: 개인/시장의 공매도 제한 → 저변동성·베타 전략은 **long-only 랭킹 전용**(음수 베타 종목 제외, short 신호 금지). 책의 저변동 'short legs'는 한국 시장에 그대로 적용 불가 → T8에서 명시.
- **상하한가(±30%)와 거래 정지**: 단일 종목의 극단 변동 → 리밸런싱 시 청산이 불가능할 수 있음 → StopLoss(time/voly stop)와 겹치되, '수급/상한가 연속상승 종목은 익절 타이밍 지연'은 후속 이슈로 문서화(이번 범위는 기본 StopLoss 7%/15%).
- **시총·거래대금 유니버스**: 소형주 유동성 리스크 → 시총 ≥ 500억, 평균 거래대금 ≥ 10억 하한. 이는 책이 대형주 중심인 것을 한국 중형주까지 실현 가능하게 보정한 것이며, '근거 규칙'으로 명시(임의 튜닝 아님).
- **재무 공시 시점 지연**: 한국 상장사 분기보고는 분기 종료 후 45일 이내 공시 → point-in-time 스냅샷이 반드시 report_date(공시완료 가정) 기준으로 리밸런싱 1일 전까지 집계해야 한다. 공시 지연(45일)을 추가 반영하는 것은 후속 개선 항목으로 기록.

### 4) 멀티팩터 결합 방법론
- 각 팩터가 단위/방향이 제각각(PE는 낮을수록, ROE는 높을수록) → **크로스섹션 Z-score로 표준화**(x-μ)/σ, 방향을 '양(+)이 좋은 값'으로 통일(PE/PBR은 -Z, ROE/모멘텀은 +Z). 동일가중 합산 → 상위 20~30. |σ|≈0 팩터는 해당 종목 제외(분모 방어).
- 책의 '팩터 결합 시 상관성 높은 팩터(가치 vs 퀄리티)의 과최적화'를 피하려고 상관도 높더라도 가중치를 동일하게 유지한다.

### 5) 리밸런싱 실전 이슈
- 재무 팩터는 분기(공시 후 1일 시점), 가격 팩터는 월간 → **실행 타이밍 차이**로 인한 중복 주문 발생 → strategy_config에 `rebalance_interval_days`(재무 63, 모멘텀 21) 명시하고, position sizer 기반 만족. 리밸런싱 이탈 종목은 sell 시그널로 정리.
- 부분 현금화·동일가중 재균형을 위한 자금 배분은 PositionSizer(기존 1M KRW base)와 충돌 → 리밸런싱 시 '포트폴리오 단위' 가중치 배분은 후속 이슈로 문서화. 이번 범위는 기존 PositionSizer 시그널 단위 크기 유지.

### 6) 과최적화 방지
- 책의 검증 규칙 임계값(상위 30/동일가중/12-1·3-6/52주/Z-score 동일가중)은 **하드코딩하고 허용 파라미터로 노출하지 않는다**(분명한 한국 시장 근거만 예외).
- 한국 데이터로 **재검증(백테스트 스모크·비교표)만** 수행하고, 성능 지표(diff)를 책 대비해 '기간·리밸런싱·유니버스' 차이를 명시. 지표가 나쁘다고 임계값을 조정하지 않는다.
- 회귀 테스트로 기존 전략 동작 불변을 지켜 신규 작업이 기존 성능을 훼손하지 않도록 한다.

### 7) Metis 검증으로 드러난 기존 결함·숨은 의존성 (반드시 반영)
계획 설계 중 코드그래프·파일 실측 검증(Metis)으로 아래를 확인했으며, 이들은 계획서 투두에 반영됨:
- **백테스트 데이터피드 컬럼명 버그**: `PGDataFeed._fetch_from_db`(`backtrader_integration.py:42-43`)는 `open/high/low/close`를 조회하지만 실스키마 컬럼은 `open_price/high_price/low_price/close_price`(`01_schema.sql:26-30`). 기존 테스트가 synthetic 경로만 커버해 버그 미발견 → 투두 11에서 수정 + DB 연동 테스트 추가.
- **재무 조회 메서드 부재**: `postgres_storage.py`(336줄)에 `financial_statements` 읽기 메서드가 전무 → 투두 1에서 `get_financial_statements(asof)` 등 신규 추가(비파괴). 이건 '있을 거라 가정'이 아니라 '없음'을 확인한 핵심 선행 작업.
- **`get_all_stocks`가 market_cap 미반환**(`postgres_storage.py:56`) → 유니버스 필터에 필요한 시총/거래대금 데이터 경로가 없음 → 투두 1·2에서 보강.
- **컬렉터가 재무 히스토리를 폐기**: `aggregate_to_financials`(`financial_collector.py:153-171`)가 최신 report_date만 유지 → point-in-time 백테스트 불가 → 투두 3에서 모든 분기 행 유지·스키마 `UNIQUE(stock_code, report_date)` 활용.
- **`StrategyWrapper`는 단일 종목 신호 재생만 지원**(`backtrader_integration.py:99-116`) → 랭크 기반 팩터 포트폴리오(상위 N/동일가중/리밸런싱) 표현 불가 → 투두 11에서 `FactorPortfolioStrategy` 신규 클래스 추가.
- **기존 3전략에 커버 테스트 전무**(blast radius 'no covering tests') → 투두 12의 회귀 베이스라인 테스트로 '비파괴'를 자동 증명해야 함.
- **`main.py`가 의도된 수정 지점**: 전략 인스턴스/블록이 하드코딩(`main.py:43-45,59-84`) → 신규 전략 등록은 `main.py` 수정을 수반(비파괴 = 기존 블록 append). 투두 10에서 명시.
- **실발행 방지는 '설계로'**: 팩터 전략 시그널이 실매매 `trade:signals`로 흘러가지 않도록 별도 `paper_only` 채널 + 활성화 플래그(`paper_only=true` 고정)로 강제(관례이 아닌 설계적 차단).
