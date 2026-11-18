# 피처 생존 판정 (3축: 분산 · 커버리지 · 누수)

- 패널: `services/xgboost-ml/app/models/wf/panel_prod200_v2.npz` — shape=(61640, 213), 기간 2025-08-04 ~ 2026-11-17 (314일), 200종목, mtime 2026-11-18 18:04:28 UTC, 스냅샷 최종일 기준 **2일 전**
- 실행: `python3 scripts/feature_survival_report.py --h 5 --min-coverage 0.05 --top-pairs 20`
- 판정: 총 213 → alive 117 · dead 69 · conditional 27

## 판정 우선순위 (먼저 걸리는 것 **하나만** 부여)

1. `DEAD_ALL_NULL` 2. `DEAD_CONST_ZERO` 3. `DEAD_CONST_OTHER` 4. `DEAD_SPARSE` 5. `CONST_MARKET` 6. `CONST_STOCK` 7. `LEAK_SUSPECT` 8. `ALIVE`

## 클래스 분포

| class | 개수 | 비율 |
|---|---|---|
| DEAD_ALL_NULL | 0 | 0.0% |
| DEAD_CONST_ZERO | 12 | 5.6% |
| DEAD_CONST_OTHER | 0 | 0.0% |
| DEAD_SPARSE | 57 | 26.8% |
| CONST_MARKET | 24 | 11.3% |
| CONST_STOCK | 0 | 0.0% |
| LEAK_SUSPECT | 3 | 1.4% |
| ALIVE | 117 | 54.9% |

## 죽은 피처 상위 20 (DEAD_*)

| # | feature | class | 사유 |
|---|---|---|---|
| 1 | authenticity_avg | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 2 | credit_balance_change | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 3 | days_to_cover | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 4 | etf_flow_5d | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 5 | event_patent_5d | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 6 | event_recall_5d | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 7 | institution_ownership_pct | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 8 | margin_balance_change | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 9 | sector_count | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 10 | sector_momentum | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 11 | short_interest_ratio | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 12 | value_ncav | DEAD_CONST_ZERO | 유효값 61640개 전부 0 |
| 13 | event_capital_increase_5d | DEAD_SPARSE | nnz_ratio_honest 0.008631 < 0.050000 |
| 14 | event_cb_bw_5d | DEAD_SPARSE | nnz_ratio_honest 0.009539 < 0.050000 |
| 15 | event_contract_5d | DEAD_SPARSE | nnz_ratio_honest 0.019225 < 0.050000 |
| 16 | event_delisting_5d | DEAD_SPARSE | nnz_ratio_honest 0.004445 < 0.050000 |
| 17 | event_disaster_5d | DEAD_SPARSE | nnz_ratio_honest 0.000941 < 0.050000 |
| 18 | event_exec_change_5d | DEAD_SPARSE | nnz_ratio_honest 0.042521 < 0.050000 |
| 19 | event_litigation_5d | DEAD_SPARSE | nnz_ratio_honest 0.003504 < 0.050000 |
| 20 | event_macro_5d | DEAD_SPARSE | nnz_ratio_honest 0.000065 < 0.050000 |

## LEAK_SUSPECT (전체 3건)

- price_vs_sector — L1=False L2=True n=60440 (의심: L1=-0.010404 L2=1.000000 (임계 L1>=0.90 L2>=0.95))
- relative_strength — L1=False L2=True n=60440 (의심: L1=-0.009853 L2=0.992464 (임계 L1>=0.90 L2>=0.95))
- return_1d — L1=False L2=True n=60440 (의심: L1=-0.010404 L2=1.000000 (임계 L1>=0.90 L2>=0.95))

- 해석: 위 3건은 전부 **L2(당일 수익률 동시상관)** 로만 걸렸고 L1(미래 h일 라벨 상관)은 0건이다. 이들은 가격 파생 피처라 설계상 동시상관이 1에 가깝다 (`return_1d = close.pct_change()`, `price_vs_sector = stock_ret_1d − sector_momentum` — services/xgboost-ml/app/feature_engine/feature_pipeline.py:286,1057-1062). 즉 **룩어헤드 증거가 아니라 정의상의 상관**이며, 확정 판정은 생성 코드 검토가 필요하다.

## 중복 컬럼 (이름이 같은 컬럼)

- 고유 이름은 199개 / 컬럼 213개 — 이름이 겹치는 쌍 **14개** (그중 값까지 동일 14개)
- 대상: `cross_trend`(col [16, 196]), `momentum_vs_volatility`(col [79, 193]), `price_volume`(col [98, 200]), `return_5d_mean_10d`(col [118, 201]), `short_medium_term_momentum`(col [133, 198]), `target_ma_10`(col [163, 211]), `target_ma_20`(col [164, 212]), `target_ma_5`(col [165, 210]), `trend_confirmation`(col [169, 199]), `trend_interaction`(col [170, 194]), `volatility_20d_mean_10d`(col [182, 202]), `volatility_volume`(col [186, 197]), `volume_price_trend`(col [187, 195]), `volume_ratio_5_mean_10d`(col [190, 203])
- 영향: 같은 정보가 두 번 들어가 피처 중요도가 쪼개지고 피처 수가 부풀려진다. 이 리포트는 **진단만** 하고 패널은 고치지 않는다(읽기 전용).

## r13 교차표

- r13 `dead_total` 35 vs 이번 실측 dead 69 — 정의·창이 달라 차이가 난다.
- r13 에서 죽었지만 이번엔 죽지 않은 피처 (19): atr_pct, basis, basis_change_5d, bayes_gain_uncertainty, bayes_momentum_1d, bayes_momentum_5d, bayes_volatility, credit_spread, derivatives_volume, futures_premium, program_trading_ratio, quality_beta, quality_price_volatility_60d, similarity_std, theme_count, theme_max_relevance, theme_momentum, twin_avg_correlation, twin_count
- 이번엔 죽었지만 r13 에 없는 피처 (20): event_capital_increase_5d, event_cb_bw_5d, event_contract_5d, event_delisting_5d, event_disaster_5d, event_exec_change_5d, event_litigation_5d, event_mna_5d, event_new_product_5d, event_partnership_5d, event_patent_5d, event_realized_5d, event_recall_5d, event_regulation_5d, event_treasury_5d, foreign_ownership_pct, kalman_activity, kalman_attention, kalman_momentum, kalman_sentiment

| r13 bucket | 이번 판정 class | 개수 |
|---|---|---|
| buildable | ALIVE | 10 |
| buildable | DEAD_CONST_ZERO | 1 |
| coverage_low | DEAD_CONST_ZERO | 4 |
| coverage_low | DEAD_SPARSE | 3 |
| market_level | CONST_MARKET | 5 |
| market_level | DEAD_SPARSE | 2 |
| source_absent | ALIVE | 2 |
| source_absent | CONST_MARKET | 1 |
| source_absent | DEAD_CONST_ZERO | 5 |
| source_absent | DEAD_SPARSE | 1 |
| source_absent | NOT_IN_PANEL | 1 |

## 한계

- 임계값(전부 `params.thresholds` 그대로 — 스펙 값, 결과에 맞춰 조정하지 않음): null_ratio>=0.999 · xsec_const>=0.95 · time_std<1e-09 · L1>=0.90 · L2>=0.95 · 누수 표본>=500 · 중복 corr>=0.9999 · min_coverage=0.050
- 누수 검사(L1/L2)는 **의심 표시일 뿐 누수를 증명하지 않는다** — 생성 코드를 봐야 확정.
- 누수 표본은 피처·fwd_ret·당일 수익률 세 값이 모두 유효한 행이며, fwd 는 코드별 정렬 후 h번째 다음 행 기준(거래일 h일, 캘린더 아님 — 코드별 결측 날짜가 있으면 어긋날 수 있다).
- xsec_constant_ratio 는 기존 정의 그대로: 그 날짜 nunique<=1 인 비율(전부 NaN 날짜 포함).
- alive/dead 집계 정의: 이 파일은 DEAD_* 4종=dead(69) · ALIVE=alive(117) · 나머지 27개는 조건부. 계기판 정의(결측 0 치환 nonzero>0)를 같은 패널에 적용하면 alive=201 dead=12 — 세 숫자가 다를 수 있으므로 계기판 n/a 공백은 이 파일의 summary 로 메운다.
- 패널 나이: 스냅샷 최종일로부터 2일 경과 — 그 사이 시장 상황과 어긋날 수 있다.
- 자기신고: source_rows=61640 · parsed_features=213 · persisted_features=213 (3분리: 소스 수신(panel 행수·바이트) / 파서 생성(통계 산출 피처 수) / 실제 저장(JSON 재독해 대조 피처 수). DB 접근 금지(스펙)로 scripts/dq_claim.py record_claim 대신 출력 파일 안 자기신고.)
