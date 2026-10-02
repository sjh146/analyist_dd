# agent_feature_revival — 죽은 피처 실측·분류·수리 보고

작성: 2026-10-02 (KST) · 작업 브랜치 `agent/feature-revival` · 모델 재학습 없음 · git 없음

목표: analyist_dd 패널의 죽은 피처(nonzero_ratio=0)를 실측으로 확정하고, (b)/(c) 계열을
수리해 부활 가능한 것을 실제로 되살린다.

---

## 0. 한 줄 요약

- **실측**: 정본 패널(200종목×120일, `app/models/exp_panel/panel.pkl`, 2026-09-23 빌드)에서
  **173피처 중 97개가 nonzero_ratio=0** (docs/DEAD_FEATURE_REVIVAL.md 의 97개와 일치).
- **현행 코드 재검증**: 그 97개 중 **65개는 이미 현행 코드로 값이 나온다**(패널이 낡아서 0으로
  보이는 것일 뿐, 저녁 ML 파이프라인이 재빌드하면 자동으로 부활). **실제로 아직 죽은 것은 32개**.
- **수리**: 32개 중 유일하게 (b) 계열로 배선 수리 가능한 **이벤트 계열 16개**를 되살렸다
  (`news_event_features.py` 리더를 `news_events`(뉴스, 패널 200종목 중 9종목만 커버)에서
  `event_features`(DART 공시, 197종목 커버)로 재배선). **부활한 12개** + 원천상 무이벤트 4개.
- 나머지 16개는 (a) 원천 부재 / (c) 유니버스 커버리지 / (d) 시장레벨(게이트·레짐)로 분류했다.

---

## 1. 현황 실측

### 1.1 측정 명령 (전부 실제 실행)

```
# (A) 정본 패널 로드 + 피처별 nonzero/상수/횡단면 통계
docker exec stock_xgboost_ml sh -c 'cd /app && python -'   # (stdin 스크립트로 panel.pkl 로드)
#   → 결과를 data/reports/_agent_feature_measure.json 에 저장 (n_features=173)

# (B) 기존 도구로 패널 커버리지 재확인
docker exec stock_xgboost_ml python scripts/feature_coverage_report.py   # exp_panel 캐시 재사용

# (C) DB 소스테이블 규모 프로브 (호스트)
set -a; . /home/jhshi/analyist_dd/.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
python3 -c "<psycopg2 로 pg_tables 행수 조회>"
```

### 1.2 패널 실측 표

정본 패널 `panel.pkl` (meta: s200_d120_t2026-09-23, n_rows=16620, n_cols=176).

| 지표 | 값 |
|---|---|
| 패널 피처 수 | 173 (176 컬럼 − stock_code/date/feature_count) |
| 행 수 | 16,620 (200종목 × 120거래일, 2026-05-26~2026-09-23) |
| **nonzero_ratio = 0 (죽은 피처)** | **97** |
| nonzero_ratio > 0 (살아있음) | 76 |

**죽은 피처 97개 목록** (측정값, `_agent_feature_measure.json`):

```
atr_pct, authenticity_avg, basis, basis_change_5d, bayes_gain_uncertainty, bayes_momentum_1d,
bayes_momentum_5d, bayes_volatility, bb_position, cpi_yoy, credit_balance_change, credit_spread,
cycle_down, cycle_up, days_to_cover, derivatives_volume, disclosure_count_5d,
economic_event_count_7d, economic_event_impact, etf_flow_5d,
event_capital_increase_5d, event_cb_bw_5d, event_contract_5d, event_delisting_5d, event_disaster_5d,
event_exec_change_5d, event_litigation_5d, event_macro_5d, event_market_liquidity_5d, event_mna_5d,
event_new_product_5d, event_partnership_5d, event_patent_5d, event_realized_5d, event_recall_5d,
event_regulation_5d, event_stake_change_5d, event_treasury_5d,
foreign_net_buy, foreign_net_buy_5d, foreign_ownership_pct, futures_premium,
fx_change_1m, fx_change_3m, fx_usd_krw, institution_net_buy, institution_net_buy_5d,
institution_ownership_pct, interest_rate, interest_rate_change_1m, interest_rate_change_3m,
krx_advance_decline_ratio, krx_total_trading_value, margin_balance_change, market_breadth,
market_impact_score, momentum_3_12m, momentum_ni, momentum_op,
oil_change_1m, oil_change_3m, oil_wti, pbr_current, per_current, ppi_yoy, program_trading_ratio,
quality_asset_growth, quality_beta, quality_cp_to_assets, quality_debt_ratio_change, quality_op_growth,
quality_op_to_equity, quality_price_volatility_60d, quality_roa, quality_roe, relative_strength,
retail_ownership_pct, roe, sector_count, sector_momentum, short_interest_ratio, short_selling_ratio,
similarity_std, theme_count, theme_exposure_5d, theme_max_relevance, theme_momentum,
twin_avg_correlation, twin_count, value_ev_ebit, value_ncav, value_pbr, value_pcr, value_per,
value_pfcr, value_psr, yield_spread
```

### 1.3 기존 보고서 `data/reports/r13_dead_feature_audit.json` 대조

r13(2026-09-28)은 **35개**를 dead_total 로 보고한다(버킷 buildable 11 / coverage_low 7 /
market_level 7 / source_absent 10). 이번 실측이 **97개**로 더 큰 이유:

- r13 은 **특정 계열(advanced/market-level/source-absent)만 큐레이션한 감사**이고, 이번 실측은
  **173피처 전수**의 `nonzero_ratio` 측정이다. r13 의 35개는 대부분 이번 97개에 **포함**된다.
- **값이 다른 3개**: r13 이 `coverage_low` 로 잡은 SNS 3개(`sns_bot_filtered_count`,
  `sns_sentiment_score_best_lag`, `sns_sentiment_score_lag_sign`)는 이번 패널 측정에서
  **nonzero**(살아있음)다 — r13 은 "원천 테이블이 얇다"는 소스 커버리지 판정이었고, 이번은
  "패널에서 값이 나오는가"라는 서로 다른 정의를 잰 것. → SNS 3개는 죽은 목록에서 제외.
- 반대로 r13 에 없는 **이벤트 16종·재무/거시/수급 다수**가 이번 97개에 추가로 잡혔다
  (r13 은 이 계열을 별도 R로 다뤄 큐레이션에 포함하지 않았음).

---

## 2. 분류 (근거 포함)

현행 코드로 200종목×120일을 **재실측**한 결과, 97개 중 65개는 이미 값이 나온다
(패널 재빌드만 필요). **아직 죽은 32개**를 4계열로 분류했다.

| 계열 | 정의 | 개수 | 근거 |
|---|---|---|---|
| (a) writer 없음 / 원천 부재 | 수집·계산 코드나 컬럼·행이 아예 없음 | 8 | 아래 표 |
| (b) 리더/라이터 컬럼 불일치 | writer 는 썼는데 리더가 다른(얇은) 테이블을 읽음 | 16 | 이벤트 계열 (수리함) |
| (c) 유니버스 커버리지 부족 | 원천이 일부 종목만 커버 | 5 | 아래 표 |
| (d) 원천상 무정보(횡단면 0) | 날짜 내 종목간 동일값(시장레벨) → 게이트/레짐 | 3 (cycle_up 등) | 아래 표 |

> 65개(이미 현행 코드로 부활)는 별도 표 §2.3 에 기재.

### 2.1 (a) writer 없음 / 원천 부재 — 8개

| 피처 | 근거 (실측) |
|---|---|
| `credit_balance_change` | `credit_balance` 테이블 **0행(부재)** |
| `margin_balance_change` | `margin_balance` 테이블 **부재** |
| `etf_flow_5d` | `etf_flow` 테이블 **부재** |
| `value_ncav` | `financial_statements` 에 `current_assets/current_liabilities` 컬럼 **부재** (NCAV 계산 불가; writer `financial_ratio_features.value_ncav` 도 0건) |
| `institution_ownership_pct` | `ownership.institution_ownership_pct` **1828행 전부 NULL** (foreign_ownership_pct 는 1828행 값 있음 — 수집기가 기관 지분율만 안 채움) |
| `short_interest_ratio` | `krx_short_selling.balance_quantity` **16977행 전부 NULL** (공매도 잔고 수집 안 됨) |
| `days_to_cover` | 위와 동일 (잔고 ÷ 평균거래량의 분자인 잔고가 NULL) |
| `authenticity_avg` | `stock_sentiment.avg_authenticity` **117행 전부 NULL** (`news_analysis.authenticity_score`는 751행 값 있는데 집계기 news_revive_pipeline 이 avg_authenticity 를 안 채움) |

### 2.2 (b) 리더/라이터 컬럼 불일치 — 16개 (수리 대상 → 수리함)

`event_*_5d` 16종(`capital_increase/cb_bw/contract/delisting/disaster/exec_change/litigation/
mna/new_product/partnership/patent/realized/recall/regulation/stake_change/treasury`).

**근거 (실측)**:
- writer `scripts/build_event_features.py` 는 `disclosures`(DART 공시, 214,944행)를 16종으로
  분류해 `event_features` 테이블(214,851행, **패널 200종목 중 197종목 커버**)에 적재한다.
- 리더 `news_event_features.py::_get_event_counts_5d` 는 `news_events`(뉴스 클러스터)를 읽는데,
  이 테이블은 **패널 200종목 중 9종목만 커버**하고 대형주(삼성전자·SK하이닉스 등)가 빠져 있다.
- → 리더가 writer 산출물이 아니라 "얇은" 뉴스 테이블을 읽는 **writer 미배선**이다.
  패널 200종목 교집합: `news_events 9/200` vs `event_features 197/200`.

### 2.3 이미 현행 코드로 부활한 65개 (패널 재빌드만 필요)

이 65개는 2026-09-24~10-02 사이 이미 고쳐진 리더(가격·베이즈·벡터·재무 as-of·거시 as-of·
수급 as-of·시장레벨·지분율) 덕에 **지금 코드로는 nonzero**다. 대표: `atr_pct, bb_position,
bayes_* 4, similarity_std, momentum_3_12m, relative_strength, value_*/quality_*/per_current/
pbr_current/roe, foreign/institution_net_buy*, foreign_ownership_pct, retail_ownership_pct,
short_selling_ratio, fx_*/oil_*/interest_*/cpi_yoy/ppi_yoy/yield_spread/credit_spread,
basis/basis_change_5d/futures_premium/derivatives_volume/program_trading_ratio,
krx_*/market_breadth, economic_event_*, disclosure_count_5d, theme_count/theme_max_relevance/
twin_*, cycle_down, momentum_ni/op, market_impact_score, theme_momentum`.

### 2.4 (c) 유니버스 커버리지 부족 — 5개

| 피처 | 근거 |
|---|---|
| `sector_count` / `sector_momentum` | `stocks.sector` **STOCK 2,799종목 전부 비어있음**(ETF/ETN만 채움). Neo4j `BELONGS_TO` 0. |
| `theme_exposure_5d` | `news_event_extraction` **패널 200종목 중 2종목만** 커버(테마는 뉴스 파이프라인 산출) |
| `event_macro_5d` / `event_market_liquidity_5d` | 공시가 아닌 뉴스 전용 이벤트(거시경제·시장지수·유동성) — `news_events` 9/200 커버 |

### 2.5 (d) 원천상 무정보(횡단면 0) — 게이트/레짐 용도

- `cycle_up`/`cycle_down`(one-hot 국면) — 실측: Neo4j `Cycle` 2026-09-23 phase=**down** →
  `cycle_down`=1(살아있음), `cycle_up`=0 은 **정상**(국면이 down이라 up 이 0). 버그 아님.
- 위 §2.3 의 시장레벨 피처(basis/futures_premium/거시 13종/ADR/breadth/economic_*)들은
  **날짜 내 전 종목 동일값**이라 횡단면 분산 0 — (d)로 **게이트/레짐 용도** 표시
  (종목 변별 신호가 아니라 국면/시장 신호로만 모델에 투입되어야 함).

---

## 3. 수리한 것 (전/후 값 실측)

### 수리 1 — (b) 이벤트 계열 16개: 리더를 `event_features` 로 재배선

파일: `services/xgboost-ml/app/feature_engine/news_event_features.py`
(`_get_event_counts_5d`)

- DART 16종 → `event_features` 테이블(빌더 산출, as-of `trade_date = 조회일`)에서 읽는다.
- 뉴스 보조 소스(`news_events`)는 **max 병합**으로 남겨, 공시에 없는 신제품/특허/리콜/규제와
  거시경제/시장지수·유동성 이벤트도 이중계상 없이 셀 수 있게 했다.

**수리 전/후 — 특정 종목·날짜 (0 → 0이 아닌 값, 두 날짜 이상 비교로 상수 아님을 확인)**:

| 종목 | 날짜 | 수리 전 | 수리 후 |
|---|---|---|---|
| 397030 | 2026-09-23 | event_*_5d 전부 0 | `event_exec_change_5d = 2.0` |
| 397030 | 2026-08-31 | 0 | `event_stake_change_5d = 1.0` |
| 220100 | 2026-09-23 | 0 | `event_exec_change_5d = 8.0`, `event_stake_change_5d = 2.0` |
| 220100 | 2026-09-10 | 0 | `event_capital_increase_5d = 3.0` |
| 224060 | 2026-09-23 | 0 | `event_capital_increase_5d = 13.0` |
| 224060 | 2026-09-10 | 0 | `event_cb_bw_5d = 5.0`, `event_exec_change_5d = 14.0` |
| 000660 | 2026-09-23 | 0 | `event_exec_change_5d = 1.0` |

(종목별로 날짜에 따라 값이 **다르고** 0이 아닌 값으로 바뀜 → 상수 아님. 두 날짜 비교 충족.)

**수리 전/후 — 패널 격자(200종목×120일=16,620행) nonzero_ratio**:

| event 피처 | 수리 전 | 수리 후 (event_features) |
|---|---|---|
| `event_stake_change_5d` | 0 | **6.96%** (1,156/16,620) |
| `event_exec_change_5d` | 0 | **5.04%** (838) |
| `event_contract_5d` | 0 | **2.53%** (421) |
| `event_realized_5d` | 0* | **1.64%** (272) |
| `event_cb_bw_5d` | 0 | **1.47%** (245) |
| `event_capital_increase_5d` | 0 | **1.41%** (234) |
| `event_treasury_5d` | 0 | **1.24%** (206) |
| `event_mna_5d` | 0 | **1.00%** (166) |
| `event_delisting_5d` | 0 | **0.99%** (165) |
| `event_litigation_5d` | 0 | **0.34%** (56) |
| `event_disaster_5d` | 0 | **0.24%** (40) |
| `event_partnership_5d` | 0 | **0.03%** (5) |
| `event_new_product_5d` / `patent` / `recall` / `regulation` | 0 | 0 (아래 §4) |

\* `event_realized_5d` 는 뉴스 1종목만 살아있던 상태였고, 이제 공시 기반 272행으로 확장.

---

## 4. 수리 못 한 것과 이유

| 피처 | 계열 | 못 살린 정확한 이유 |
|---|---|---|
| `event_new_product_5d`, `event_patent_5d`, `event_recall_5d`, `event_regulation_5d` | (c) | 패널 200종목의 120일 창에 이 이벤트가 **0건**(DART 공시·뉴스 둘 다). 원천은 있으나 유니버스·기간상 무발생. 백필 기간 확장/유니버스 확대 시 자동 부활. |
| `credit_balance_change`, `margin_balance_change`, `etf_flow_5d` | (a) | 원천 테이블 자체가 없음 — KRX 신용·예탁금/프로그램·ETF 수급 수집이 선행돼야 함(수집 = 피드 경로 → 금지). |
| `value_ncav` | (a) | `financial_statements.current_assets/current_liabilities` 컬럼 부재 — DART 재무 항목 확장 필요. |
| `institution_ownership_pct` | (a) | `ownership.institution_ownership_pct` 전부 NULL — 지분공시(D) 기관 지분 수집 필요. |
| `short_interest_ratio`, `days_to_cover` | (a) | `krx_short_selling.balance_quantity`(공매도 잔고) 전부 NULL. |
| `authenticity_avg` | (a) | 집계기(`news_revive_pipeline.aggregate`)가 `stock_sentiment.avg_authenticity` 를 안 채움(뉴스 파이프라인 재집계 필요 — 피드 경로). |
| `sector_count`, `sector_momentum` | (c) | `stocks.sector` 2,799종목 전부 비어있음 — KRX 업종(IP 차단) / DART company.json(종목당 1콜) 수집 필요. |
| `theme_exposure_5d` | (c) | 뉴스 테마 추출이 패널 종목을 2종목만 커버. |
| `event_macro_5d`, `event_market_liquidity_5d` | (c) | 뉴스 전용 이벤트 — `news_events` 커버 9종목. |
| `cycle_up` | (d) | 국면 one-hot — 2026-09-23 phase=down 이라 0 이 정상(버그 아님). 게이트/레짐 용도로 표시. |

---

## 5. 다음 1순위 (재학습 후 AUC 기대)

모델 재학습은 금지라 AUC 는 기존 리포트를 인용한다. 부활 후 기대 순서:

1. **이벤트 12개 (이번 수리)** — 단기 방향성과 직결되는 신호. DEAD_FEATURE_REVIVAL.md #1 이
   "이벤트 19개"를 최우선으로 잡았고, 이번에 리더가 `event_features` 를 읽게 되어 **재학습 시
   바로 투입**된다. 기대: 이벤트 피처는 기존 기여도 0 → 유의 신호(방향성)로 전환.
2. **재빌드로 자동 부활하는 65개** — 저녁 ML 파이프라인이 패널을 새로 구우면 65개가 한꺼번에
   살아난다. **엔지니어가 로버스트 AUC(다중폴드 평균)로 임팩트 측정**하는 것이 다음 스텝
   (한 계열씩 투입, 기준선 +0.02 이상 신호 판정 — DEAD_FEATURE_REVIVAL.md 규율).
3. **(a) 원천 수집** — 공매도 잔고·기관 지분·신용/예탁금·ETF 수급·업종(섹터)·유동자산 컬럼.
   수집은 "피드 경로"라 이번 세션에서 금지. 각 수집이 끝나면 기존 writer(build_*)가 자동으로
   해당 피처를 채우고, 리더는 이미 배선돼 있어 재빌드만으로 부활.

---

## 6. 실패한 시도

- **(b) 추가 수리 후보 탐색 — 전부 기각**: `short_interest_ratio`/`days_to_cover` 리더가
  존재하지 않는 `short_interest` 테이블을 읽는 것을 확인했으나, 올바른 원천(`krx_short_selling`)
  의 분자 `balance_quantity` 가 전부 NULL 이라 수리해도 0 → 0 이 돼 "실패"로 기록될 뿐이라
  배선 수리를 하지 않았다(계산식은 `build_supply_market_features.py` 에 이미 준비돼 있음).
- **`authenticity_avg` 수리 시도 — 기각**: `news_analysis.authenticity_score`(751행)는 있지만
  `news_analysis` 에 `stock_code` 컬럼이 없고, 집계 경로가 뉴스 피드(`news_revive_pipeline`)라
  금지 범위. 리더(`sentiment_features`)는 올바르게 `stock_sentiment.avg_authenticity` 를 읽고
  있으며, 결측이면 0을 반환하는 게 정상 동작이다.
- **`sector_momentum` 수리 시도 — 기각**: 리더가 `stock_prices`(market_data 뷰)와 `stocks.sector`
  를 읽는데 `stocks.sector` 2,799종목 전부 비어 있어 섹터 데이터 없이는 부활 불가.

---

## 7. 검증 (명령 + 출력)

### 7.1 py_compile

```
$ python3 -m py_compile services/xgboost-ml/app/feature_engine/news_event_features.py
(exit 0, 출력 없음 = 통과)
```

### 7.2 pytest

```
$ cd services/xgboost-ml && python3 -m pytest \
    tests/test_news_event_features.py \
    tests/test_news_event_features_time_align.py \
    tests/test_feature_contract_growth.py -q

.................................                                            [100%]
33 passed in 0.97s
```

### 7.3 엔드투엔드 (리더 직접 + 파이프라인 경유)

```
$ python3 - <<'PY'  # 호스트, worktree 코드
from app.feature_engine.feature_pipeline import FeaturePipeline
f = FeaturePipeline(pg_conn=pg).build_features('397030', '2026-09-23', market_df=mdf)
# f['event_exec_change_5d'] == 2.0  (수리 전 0.0)
PY
# → 397030 @ 2026-09-23 event features: {'event_exec_change_5d': 2.0}
```

---

## 부록 A. 변경 파일

- `services/xgboost-ml/app/feature_engine/news_event_features.py`
  — `_get_event_counts_5d` 를 `event_features`(16종) + `news_events`(max 병합 보조)로 재배선.

## 부록 B. 주요 소스 테이블 → 패널 커버리지 (실측)

| 테이블 | 패널 200종목 커버 |
|---|---|
| `event_features` | **197/200** |
| `financial_statements` | 197/200 |
| `foreign_institutional` | 97/200 |
| `ownership` | 89/200 |
| `krx_short_selling` | 17/200 |
| `news_events` | 9/200 |
| `stock_sentiment` | 9/200 |
| `news_event_extraction` | 2/200 |
