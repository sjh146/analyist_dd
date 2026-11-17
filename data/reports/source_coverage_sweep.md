# 미배선 원천 전수 스윕 — 2026-11-17T07:07:43+09:00

- 스냅샷: `panel_prod200.npz` [54800, 213] · 279일 (2025-08-04~2026-09-23) · mtime 2026-10-02T18:25:21
- 게이트: 종목코드 + 날짜 컬럼 + 커버리지 ≥ 0.75 + 거래일 ≥ 250 (패널 유니버스 200)
- **결과: 신규 후보 없음 (BLOCKED 2 · CLOSED 9 · 시장레벨 13 → 남은 레버는 수집 데이터뿐)**

| 판정 | 테이블 | 행 | 종목 | 거래일 | 패널커버 | 사유 |
|---|---|---|---|---|---|---|
| BLOCKED | `stock_vectors` | 4350 | 4350 | 7 | 1.0 |  |
| BLOCKED | `stocks` | 4350 | 4350 | 7 | 1.0 |  |
| CLOSED | `foreign_institutional` | 194092 | 1166 | 305 | 0.455 | 커버리지 87/200 — 값 보유 338종목 · 단변량 IC +0.0045~+0.0082(t<0.7) = 무정보 실측 |
| CLOSED | `ownership` | 5244 | 1073 | 16 | 0.41 | 이력 9일 — 소스 미축적 |
| CLOSED | `sns_post_features` | 18807 | 366 | 234 | 0.145 | 위와 동일 원천의 피처 테이블 |
| CLOSED | `sns_posts` | 430796 | 366 | 257 | 0.145 | 커버리지 22/200 · 이력 190일 — CG73/CG60 |
| CLOSED | `krx_short_selling` | 23833 | 305 | 117 | 0.125 | 수집 범위 11/200 — CG141 |
| CLOSED | `news_events` | 12229 | 425 | 201 | 0.12 | 커버리지 15/200 · 이력 192일 — CG73/CG60 |
| CLOSED | `minute_bars` | 118290 | 301 | 15 | 0.115 | XR26 미수리(30봉 꼬리) — CG129/CG101 |
| CLOSED | `stock_sentiment` | 91 | 78 | 2 | 0.02 | 이력 3일 — 신설 |
| CLOSED | `news_event_extraction` | 441 | 63 | 2 | 0.005 | 이력 4일 — 신설 |
| WIRED | `market_data` | 3223794 | 3980 | 944 | 1.0 |  |
| WIRED | `ml_predictions` | 102566 | 4350 | 24 | 1.0 |  |
| WIRED | `stock_prices` | 3153592 | 3980 | 944 | 1.0 |  |
| WIRED | `supply_market_features` | 1198283 | 3959 | 347 | 1.0 |  |
| WIRED | `disclosures` | 223453 | 3077 | 455 | 0.955 |  |
| WIRED | `event_features` | 222007 | 2711 | 324 | 0.955 |  |
| WIRED | `financial_ratio_features` | 856301 | 2594 | 347 | 0.95 |  |
| WIRED | `financial_statements` | 10370 | 2603 | 2 | 0.95 |  |
| STATIC | `trader_fills` | 8 |  |  | 0.0 |  |
| MARKET_LEVEL | `dq_runner_claim` | 328 |  |  |  |  |
| MARKET_LEVEL | `economic_events` | 318 |  |  |  |  |
| MARKET_LEVEL | `feature_coverage` | 202 |  |  |  |  |
| MARKET_LEVEL | `futures_options` | 113 |  |  |  |  |
| MARKET_LEVEL | `krx_derivatives` | 3444 |  |  |  |  |
| MARKET_LEVEL | `krx_program_trading` | 232 |  |  |  |  |
| MARKET_LEVEL | `krx_trading` | 504 |  |  |  |  |
| MARKET_LEVEL | `macro_features` | 944 |  |  |  |  |
| MARKET_LEVEL | `macro_indicators` | 47037 |  |  |  |  |
| MARKET_LEVEL | `news_analysis` | 393 |  |  |  |  |
| MARKET_LEVEL | `strategy_config` | 6 |  |  |  |  |
| MARKET_LEVEL | `strategy_runs` | 59 |  |  |  |  |
| MARKET_LEVEL | `us_market_data` | 151 |  |  |  |  |

판정 뜻: WIRED=이미 피처 배선 · CLOSED=실측 무정보/부분커버 확정 · MARKET_LEVEL=종목축 없음(횡단면 입력 불가) · STATIC=종목상수/메타 · BLOCKED=게이트 미달 · NEW_CANDIDATE=피처 배선 실험 등록 대상
