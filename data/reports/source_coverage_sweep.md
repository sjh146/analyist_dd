# 미배선 원천 전수 스윕 — 2026-11-24T22:29:28+09:00

- 스냅샷: `panel_prod200.npz` [54800, 213] · 279일 (2025-08-04~2026-09-23) · mtime 2026-10-02T18:25:21
- 게이트: 종목코드 + 날짜 컬럼 + 커버리지 ≥ 0.75 + 거래일 ≥ 250 (패널 유니버스 200)
- **결과: 신규 후보 없음 (BLOCKED 5 · CLOSED 10 · 시장레벨 19 → 남은 레버는 수집 데이터뿐)**

| 판정 | 테이블 | 행 | 종목 | 거래일 | 패널커버 | 사유 |
|---|---|---|---|---|---|---|
| BLOCKED | `stock_vectors` | 4354 | 4354 | 10 | 1.0 |  |
| BLOCKED | `stocks` | 4354 | 4354 | 10 | 1.0 |  |
| BLOCKED | `minute_close_bars` | 1152236 | 821 | 10 | 0.335 |  |
| BLOCKED | `news_mention_features` | 6398 | 193 | 62 | 0.11 |  |
| BLOCKED | `etf_premium_snapshots` | 141984 | 102 | 5 | 0.0 |  |
| CLOSED | `krx_short_selling` | 265290 | 2628 | 161 | 1.0 | 수집 범위 11/200 — CG141 |
| CLOSED | `major_holders` | 6267 | 2632 | 486 | 0.845 | CG465 A/B 무효(2026-11-24): AUC 짝Δ −0.0028(1/5) · 순기대 Δ≤|0.20|(t≤1.12) |
| CLOSED | `foreign_institutional` | 199648 | 1181 | 310 | 0.47 | 커버리지 87/200 — 값 보유 338종목 · 단변량 IC +0.0045~+0.0082(t<0.7) = 무정보 실측 |
| CLOSED | `ownership` | 8490 | 1089 | 21 | 0.425 | 이력 9일 — 소스 미축적 |
| CLOSED | `news_events` | 14649 | 554 | 209 | 0.185 | 커버리지 15/200 · 이력 192일 — CG73/CG60 |
| CLOSED | `sns_post_features` | 22231 | 531 | 245 | 0.185 | 위와 동일 원천의 피처 테이블 |
| CLOSED | `sns_posts` | 546141 | 539 | 756 | 0.185 | 커버리지 22/200 · 이력 190일 — CG73/CG60 |
| CLOSED | `minute_bars` | 154290 | 301 | 19 | 0.115 | XR26 미수리(30봉 꼬리) — CG129/CG101 |
| CLOSED | `stock_sentiment` | 534 | 263 | 10 | 0.11 | 이력 3일 — 신설 |
| CLOSED | `news_event_extraction` | 2820 | 271 | 11 | 0.08 | 이력 4일 — 신설 |
| WIRED | `market_data` | 3243085 | 3984 | 949 | 1.0 |  |
| WIRED | `ml_predictions` | 127908 | 4354 | 32 | 1.0 |  |
| WIRED | `stock_prices` | 3172220 | 3984 | 949 | 1.0 |  |
| WIRED | `supply_market_features` | 1224614 | 3962 | 353 | 1.0 |  |
| WIRED | `disclosures` | 227257 | 3082 | 462 | 0.955 |  |
| WIRED | `event_features` | 238142 | 2717 | 350 | 0.955 |  |
| WIRED | `event_rolling_features` | 777374 | 2709 | 290 | 0.955 |  |
| WIRED | `financial_ratio_features` | 872171 | 2589 | 353 | 0.95 |  |
| WIRED | `financial_statements` | 12948 | 2618 | 3 | 0.95 |  |
| STATIC | `stock_themes` | 1645 |  |  | 0.59 |  |
| STATIC | `institutional_ownership` | 1296 | 1296 | 1 | 0.44 |  |
| STATIC | `news_stock_mentions` | 9384 | 193 | 1 | 0.11 |  |
| STATIC | `trader_fills` | 27 |  |  | 0.01 |  |
| STATIC | `trade_loss_reviews` | 15 | 15 | 5 | 0.005 |  |
| STATIC | `position_theses` | 2 | 2 | 1 | 0.0 |  |
| MARKET_LEVEL | `dq_runner_claim` | 2118 |  |  |  |  |
| MARKET_LEVEL | `economic_events` | 318 |  |  |  |  |
| MARKET_LEVEL | `feature_coverage` | 209 |  |  |  |  |
| MARKET_LEVEL | `futures_options` | 120 |  |  |  |  |
| MARKET_LEVEL | `index_daily` | 918 |  |  |  |  |
| MARKET_LEVEL | `krx_derivatives` | 3652 |  |  |  |  |
| MARKET_LEVEL | `krx_program_trading` | 246 |  |  |  |  |
| MARKET_LEVEL | `krx_trading` | 504 |  |  |  |  |
| MARKET_LEVEL | `macro_features` | 353 |  |  |  |  |
| MARKET_LEVEL | `macro_indicators` | 47088 |  |  |  |  |
| MARKET_LEVEL | `news_analysis` | 2507 |  |  |  |  |
| MARKET_LEVEL | `news_stock_match_failures` | 38 |  |  |  |  |
| MARKET_LEVEL | `strategy_config` | 6 |  |  |  |  |
| MARKET_LEVEL | `strategy_runs` | 74 |  |  |  |  |
| MARKET_LEVEL | `theme_daily` | 760 |  |  |  |  |
| MARKET_LEVEL | `themes` | 20 |  |  |  |  |
| MARKET_LEVEL | `thesis_evidence` | 2 |  |  |  |  |
| MARKET_LEVEL | `thesis_verdicts` | 16 |  |  |  |  |
| MARKET_LEVEL | `us_market_data` | 181 |  |  |  |  |

판정 뜻: WIRED=이미 피처 배선 · CLOSED=실측 무정보/부분커버 확정 · MARKET_LEVEL=종목축 없음(횡단면 입력 불가) · STATIC=종목상수/메타 · BLOCKED=게이트 미달 · NEW_CANDIDATE=피처 배선 실험 등록 대상
