# 미배선 원천 전수 스윕 — 2026-11-20T19:11:06+09:00

- 스냅샷: `panel_prod200.npz` [54800, 213] · 279일 (2025-08-04~2026-09-23) · mtime 2026-10-02T18:25:21
- 게이트: 종목코드 + 날짜 컬럼 + 커버리지 ≥ 0.75 + 거래일 ≥ 250 (패널 유니버스 200)
- **결과: 신규 후보 event_rolling_features → 피처 배선 실험 등록 필요**

| 판정 | 테이블 | 행 | 종목 | 거래일 | 패널커버 | 사유 |
|---|---|---|---|---|---|---|
| NEW_CANDIDATE | `event_rolling_features` | 780091 | 2710 | 291 | 0.955 |  |
| BLOCKED | `stock_vectors` | 4353 | 4353 | 9 | 1.0 |  |
| BLOCKED | `stocks` | 4353 | 4353 | 9 | 1.0 |  |
| BLOCKED | `minute_close_bars` | 725735 | 428 | 8 | 0.145 |  |
| BLOCKED | `news_mention_features` | 6398 | 193 | 62 | 0.11 |  |
| BLOCKED | `etf_premium_snapshots` | 62322 | 102 | 3 | 0.0 |  |
| CLOSED | `krx_short_selling` | 264690 | 2627 | 158 | 1.0 | 수집 범위 11/200 — CG141 |
| CLOSED | `foreign_institutional` | 197681 | 1178 | 308 | 0.47 | 커버리지 87/200 — 값 보유 338종목 · 단변량 IC +0.0045~+0.0082(t<0.7) = 무정보 실측 |
| CLOSED | `ownership` | 7498 | 1086 | 19 | 0.425 | 이력 9일 — 소스 미축적 |
| CLOSED | `news_events` | 13515 | 509 | 205 | 0.165 | 커버리지 15/200 · 이력 192일 — CG73/CG60 |
| CLOSED | `sns_posts` | 505753 | 485 | 657 | 0.165 | 커버리지 22/200 · 이력 190일 — CG73/CG60 |
| CLOSED | `sns_post_features` | 18807 | 366 | 234 | 0.145 | 위와 동일 원천의 피처 테이블 |
| CLOSED | `minute_bars` | 145290 | 301 | 18 | 0.115 | XR26 미수리(30봉 꼬리) — CG129/CG101 |
| CLOSED | `stock_sentiment` | 353 | 213 | 6 | 0.07 | 이력 3일 — 신설 |
| CLOSED | `news_event_extraction` | 1708 | 202 | 7 | 0.055 | 이력 4일 — 신설 |
| WIRED | `market_data` | 3235415 | 3983 | 947 | 1.0 |  |
| WIRED | `ml_predictions` | 112066 | 4353 | 27 | 1.0 |  |
| WIRED | `stock_prices` | 3164773 | 3983 | 947 | 1.0 |  |
| WIRED | `supply_market_features` | 1213311 | 3961 | 351 | 1.0 |  |
| WIRED | `disclosures` | 225011 | 3082 | 460 | 0.955 |  |
| WIRED | `event_features` | 223232 | 2711 | 324 | 0.955 |  |
| WIRED | `financial_ratio_features` | 861226 | 2603 | 349 | 0.95 |  |
| WIRED | `financial_statements` | 10370 | 2603 | 2 | 0.95 |  |
| STATIC | `news_stock_mentions` | 9384 | 193 | 1 | 0.11 |  |
| STATIC | `trader_fills` | 21 |  |  | 0.005 |  |
| STATIC | `position_theses` | 2 | 2 | 1 | 0.0 |  |
| STATIC | `trade_loss_reviews` | 14 | 14 | 4 | 0.0 |  |
| MARKET_LEVEL | `dq_runner_claim` | 1129 |  |  |  |  |
| MARKET_LEVEL | `economic_events` | 318 |  |  |  |  |
| MARKET_LEVEL | `feature_coverage` | 202 |  |  |  |  |
| MARKET_LEVEL | `futures_options` | 117 |  |  |  |  |
| MARKET_LEVEL | `index_daily` | 918 |  |  |  |  |
| MARKET_LEVEL | `krx_derivatives` | 3564 |  |  |  |  |
| MARKET_LEVEL | `krx_program_trading` | 240 |  |  |  |  |
| MARKET_LEVEL | `krx_trading` | 504 |  |  |  |  |
| MARKET_LEVEL | `macro_features` | 349 |  |  |  |  |
| MARKET_LEVEL | `macro_indicators` | 47062 |  |  |  |  |
| MARKET_LEVEL | `news_analysis` | 1520 |  |  |  |  |
| MARKET_LEVEL | `news_stock_match_failures` | 1 |  |  |  |  |
| MARKET_LEVEL | `strategy_config` | 6 |  |  |  |  |
| MARKET_LEVEL | `strategy_runs` | 68 |  |  |  |  |
| MARKET_LEVEL | `thesis_evidence` | 2 |  |  |  |  |
| MARKET_LEVEL | `thesis_verdicts` | 8 |  |  |  |  |
| MARKET_LEVEL | `us_market_data` | 169 |  |  |  |  |

판정 뜻: WIRED=이미 피처 배선 · CLOSED=실측 무정보/부분커버 확정 · MARKET_LEVEL=종목축 없음(횡단면 입력 불가) · STATIC=종목상수/메타 · BLOCKED=게이트 미달 · NEW_CANDIDATE=피처 배선 실험 등록 대상
