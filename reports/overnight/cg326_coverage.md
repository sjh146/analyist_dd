# 미배선 원천 전수 스윕 — 2026-11-18T06:24:26+09:00

- 스냅샷: `panel_200liq_ffill_2605_ss_tv.npz` [22382, 225] · 112일 (2026-05-27~2026-11-09) · mtime 2026-11-17T21:10:52
- 게이트: 종목코드 + 날짜 컬럼 + 커버리지 ≥ 0.75 + 거래일 ≥ 250 (패널 유니버스 200)
- **결과: 신규 후보 없음 (BLOCKED 3 · CLOSED 9 · 시장레벨 15 → 남은 레버는 수집 데이터뿐)**

| 판정 | 테이블 | 행 | 종목 | 거래일 | 패널커버 | 사유 |
|---|---|---|---|---|---|---|
| BLOCKED | `stock_vectors` | 4350 | 4350 | 7 | 1.0 |  |
| BLOCKED | `stocks` | 4350 | 4350 | 7 | 1.0 |  |
| BLOCKED | `minute_close_bars` | 561081 | 297 | 6 | 0.195 |  |
| CLOSED | `foreign_institutional` | 195535 | 1168 | 306 | 0.97 | 커버리지 87/200 — 값 보유 338종목 · 단변량 IC +0.0045~+0.0082(t<0.7) = 무정보 실측 |
| CLOSED | `ownership` | 5898 | 1075 | 17 | 0.97 | 이력 9일 — 소스 미축적 |
| CLOSED | `sns_posts` | 458394 | 441 | 534 | 0.9 | 커버리지 22/200 · 이력 190일 — CG73/CG60 |
| CLOSED | `krx_short_selling` | 24472 | 306 | 119 | 0.88 | 수집 범위 11/200 — CG141 |
| CLOSED | `sns_post_features` | 18807 | 366 | 234 | 0.875 | 위와 동일 원천의 피처 테이블 |
| CLOSED | `minute_bars` | 127290 | 301 | 16 | 0.82 | XR26 미수리(30봉 꼬리) — CG129/CG101 |
| CLOSED | `news_events` | 12448 | 450 | 202 | 0.54 | 커버리지 15/200 · 이력 192일 — CG73/CG60 |
| CLOSED | `stock_sentiment` | 137 | 107 | 3 | 0.24 | 이력 3일 — 신설 |
| CLOSED | `news_event_extraction` | 649 | 100 | 3 | 0.15 | 이력 4일 — 신설 |
| WIRED | `market_data` | 3227630 | 3980 | 945 | 1.0 |  |
| WIRED | `ml_predictions` | 105731 | 4350 | 25 | 1.0 |  |
| WIRED | `stock_prices` | 3157428 | 3980 | 945 | 1.0 |  |
| WIRED | `supply_market_features` | 1203533 | 3958 | 349 | 1.0 |  |
| WIRED | `disclosures` | 224094 | 3077 | 457 | 0.955 |  |
| WIRED | `event_features` | 222822 | 2710 | 325 | 0.955 |  |
| WIRED | `financial_ratio_features` | 861226 | 2603 | 349 | 0.955 |  |
| WIRED | `financial_statements` | 10370 | 2603 | 2 | 0.955 |  |
| STATIC | `trader_fills` | 10 |  |  | 0.015 |  |
| STATIC | `trade_loss_reviews` | 6 | 6 | 1 | 0.01 |  |
| STATIC | `position_theses` | 2 | 2 | 1 | 0.0 |  |
| MARKET_LEVEL | `dq_runner_claim` | 373 |  |  |  |  |
| MARKET_LEVEL | `economic_events` | 318 |  |  |  |  |
| MARKET_LEVEL | `feature_coverage` | 202 |  |  |  |  |
| MARKET_LEVEL | `futures_options` | 115 |  |  |  |  |
| MARKET_LEVEL | `krx_derivatives` | 3503 |  |  |  |  |
| MARKET_LEVEL | `krx_program_trading` | 236 |  |  |  |  |
| MARKET_LEVEL | `krx_trading` | 504 |  |  |  |  |
| MARKET_LEVEL | `macro_features` | 349 |  |  |  |  |
| MARKET_LEVEL | `macro_indicators` | 47052 |  |  |  |  |
| MARKET_LEVEL | `news_analysis` | 581 |  |  |  |  |
| MARKET_LEVEL | `strategy_config` | 6 |  |  |  |  |
| MARKET_LEVEL | `strategy_runs` | 62 |  |  |  |  |
| MARKET_LEVEL | `thesis_evidence` | 2 |  |  |  |  |
| MARKET_LEVEL | `thesis_verdicts` | 2 |  |  |  |  |
| MARKET_LEVEL | `us_market_data` | 151 |  |  |  |  |

판정 뜻: WIRED=이미 피처 배선 · CLOSED=실측 무정보/부분커버 확정 · MARKET_LEVEL=종목축 없음(횡단면 입력 불가) · STATIC=종목상수/메타 · BLOCKED=게이트 미달 · NEW_CANDIDATE=피처 배선 실험 등록 대상
