# 미배선 원천 전수 스윕 — 2026-10-10T19:06:22+09:00

- 스냅샷: `panel_prod200.npz` [54800, 213] · 279일 (2025-08-04~2026-09-23) · mtime 2026-10-02T18:25:21
- 게이트: 종목코드 + 날짜 컬럼 + 커버리지 ≥ 0.75 + 거래일 ≥ 250 (패널 유니버스 200)
- **결과: 신규 후보 없음 (BLOCKED 0 · CLOSED 9 · 시장레벨 12 → 남은 레버는 수집 데이터뿐)**

| 판정 | 테이블 | 행 | 종목 | 거래일 | 패널커버 | 사유 |
|---|---|---|---|---|---|---|
| CLOSED | `foreign_institutional` | 148344 | 1071 | 279 | 0.435 | 커버리지 87/200 — 값 보유 338종목 · 단변량 IC +0.0045~+0.0082(t<0.7) = 무정보 실측 |
| CLOSED | `ownership` | 3080 | 973 | 9 | 0.39 | 이력 9일 — 소스 미축적 |
| CLOSED | `minute_bars` | 72120 | 301 | 9 | 0.115 | XR26 미수리(30봉 꼬리) — CG129/CG101 |
| CLOSED | `sns_post_features` | 16114 | 309 | 190 | 0.11 | 위와 동일 원천의 피처 테이블 |
| CLOSED | `sns_posts` | 384821 | 309 | 190 | 0.11 | 커버리지 22/200 · 이력 190일 — CG73/CG60 |
| CLOSED | `news_events` | 9750 | 287 | 192 | 0.075 | 커버리지 15/200 · 이력 192일 — CG73/CG60 |
| CLOSED | `krx_short_selling` | 17988 | 210 | 92 | 0.055 | 수집 범위 11/200 — CG141 |
| CLOSED | `stock_sentiment` | 92 | 74 | 3 | 0.03 | 이력 3일 — 신설 |
| CLOSED | `news_event_extraction` | 700 | 60 | 4 | 0.01 | 이력 4일 — 신설 |
| WIRED | `market_data` | 3123894 | 3973 | 918 | 1.0 |  |
| WIRED | `ml_predictions` | 67779 | 4343 | 16 | 1.0 |  |
| WIRED | `stock_prices` | 3055959 | 3973 | 918 | 1.0 |  |
| WIRED | `supply_market_features` | 1082213 | 3934 | 316 | 1.0 |  |
| WIRED | `disclosures` | 216199 | 3058 | 430 | 0.955 |  |
| WIRED | `event_features` | 216752 | 2705 | 315 | 0.955 |  |
| WIRED | `financial_ratio_features` | 779914 | 2571 | 316 | 0.95 |  |
| WIRED | `financial_statements` | 10370 | 2603 | 2 | 0.95 |  |
| STATIC | `stock_vectors` | 4343 | 4343 | 5 | 1.0 |  |
| STATIC | `stocks` | 4343 | 4343 | 5 | 1.0 |  |
| STATIC | `trader_fills` | 4 |  |  | 0.0 |  |
| MARKET_LEVEL | `dq_runner_claim` | 186 |  |  |  |  |
| MARKET_LEVEL | `economic_events` | 291 |  |  |  |  |
| MARKET_LEVEL | `feature_coverage` | 202 |  |  |  |  |
| MARKET_LEVEL | `futures_options` | 106 |  |  |  |  |
| MARKET_LEVEL | `krx_derivatives` | 3242 |  |  |  |  |
| MARKET_LEVEL | `krx_program_trading` | 212 |  |  |  |  |
| MARKET_LEVEL | `krx_trading` | 504 |  |  |  |  |
| MARKET_LEVEL | `macro_features` | 315 |  |  |  |  |
| MARKET_LEVEL | `macro_indicators` | 6334 |  |  |  |  |
| MARKET_LEVEL | `news_analysis` | 700 |  |  |  |  |
| MARKET_LEVEL | `strategy_config` | 6 |  |  |  |  |
| MARKET_LEVEL | `strategy_runs` | 43 |  |  |  |  |

판정 뜻: WIRED=이미 피처 배선 · CLOSED=실측 무정보/부분커버 확정 · MARKET_LEVEL=종목축 없음(횡단면 입력 불가) · STATIC=종목상수/메타 · BLOCKED=게이트 미달 · NEW_CANDIDATE=피처 배선 실험 등록 대상
