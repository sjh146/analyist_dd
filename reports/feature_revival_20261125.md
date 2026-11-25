# 피처 사망 원인 규명 · 부활 · 검증 (2026-11-25 야간)

## 1. 죽은 원인 (실측)

| 피처 | 원인 | 근거 |
|---|---|---|
| event_partnership_5d | 원천 희소 — DART 제목 키워드 불일치 | 15개월 공시 3건(패널 유니버스) |
| event_recall_5d | 원천 희소 | 2건 |
| event_patent_5d | 원천 희소 | 1건 |
| event_new_product_5d | 원천 희소(경계) | 39건 = 0.06% |
| event_regulation_5d | 원천 희소 | 7건 |
| event_market_liquidity_5d | 컬럼 자체가 없음(계산 경로 부재) | event_features 스키마 확인 |
| sentiment_avg_db / sentiment_momentum / authenticity_avg | SNS 원천이 패널 유니버스와 거의 불일치 | 패널 200종목 중 36종목만 보유, nonzero 0.01% |
| theme_momentum | 테마 일별 집계 크론 실패 | `no password supplied` → theme_daily 11-23 정지 |
| sector_count | 정의상 상수(종목당 섹터 1개) + 그래프에 ETF/ETN만 존재 | Neo4j Sector 2개(ETF 1,175·ETN 369) |
| credit_balance_change / margin_balance_change / etf_flow_5d | 국내 공개 API 원천 없음 | KIS·KRX 미제공 |

## 2. 고친 것

1. **테마 빌더 크론 수리** (`f39c744`)
   - 스크립트가 `.env`를 읽지 않아 매일 18:50 실패 (`no password supplied`)
   - 호스트에서 `.env`의 `POSTGRES_PORT=5432`를 그대로 써 접속 실패 → 호스트는 5434로 보정
   - 결과: theme_daily **760행/38일(11-23 정지) → 1,179행/59일(11-25)**

2. **패널 유니버스 SNS 수집 추가** (`d00f383`, 크론 17:35)
   - 기존 수집은 "당일 거래대금 상위 250"만 → 패널 200종목과 교집합 36종목
   - 패널 200종목 전용 수집 실행: 게시글 24,041건 수집 / 19,450건 신규 저장
   - 집계 writer 실행: sns_post_features **22,690 → 26,898행**
   - 패널 커버리지 **36종목 → 130종목(65%)**, 패널 (종목,일) 0.01% → 4.9%

3. **커버리지 판정을 패널 실측으로 교체** (`6694055`)
   - feature_coverage가 빌더별 조각 갱신이라 6개 피처가 11-23 값에 고정
   - `scripts/refresh_coverage_from_panel.py`로 패널 기준 재계산

## 3. 검증 (패널 200종목 × 314일, 5거래일 후 수익)

| 피처 | 유효값 | 비영 | 상위-하위 5일 스프레드 | t | 폴드 |
|---|---|---|---|---|---|
| **theme_momentum** | 100% | 10.5% | **+0.841%** | **+2.50** | 47 |
| sns_momentum_score | 4.2% | 4.1% | -0.829% | -1.11 | 7 |
| kalman_sentiment | 4.2% | 3.3% | +0.352% | +0.50 | 12 |
| sns_post_count | 4.2% | 4.2% | -1.042% | -1.32 | 12 |
| sns_author_quality_score | 4.2% | 2.3% | -0.308% | -0.55 | 12 |

- **theme_momentum은 살아났고 통계적으로 유의**(t=2.50). 왕복 비용 0.21%를 넘는 스프레드.
- SNS 3종은 커버리지는 65%로 올랐지만 패널 이력이 4.2%뿐이라 **판정 불가**(|t|<1.4). 2~3개월 축적 후 재판정.
- sector_count·이벤트 4종은 원천 부재/상수라 **재학습 때 제거 권고**.

## 4. 다음 재학습에 반영할 것

- 포함: theme_momentum(패널 v8에 컬럼 추가됨), SNS 계열(데이터 축적 중)
- 제외: event_partnership/recall/patent/new_product/regulation/market_liquidity, sector_count,
  credit_balance_change, margin_balance_change, etf_flow_5d

## 5. 크론 상태

```
18:50 build_theme_map.py      (수리됨 — theme_daily 매일 갱신)
17:35 sns_collect_panel.sh    (신규 — 패널 200종목 종토방 수집)
18:20 sns_features.sh         (기존 — 집계 writer)
07:00 / 16:10 feature_liveness_check.py  (패널 기준 판정)
```
