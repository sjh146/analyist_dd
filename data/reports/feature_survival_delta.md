# 피처 생존 v1 → v2 델타 (2026-11-18)

| 구분 | v1 | v2 |
|---|---|---|
| 패널 | [54800, 213] 2025-08-04~2026-09-23 | [61640, 213] 2025-08-04~2026-11-17 |
| alive / dead / 조건부 | 117 / 69 / 27 | 117 / 69 / 27 |
| DEAD_CONST_ZERO | 24 | 12 |
| DEAD_SPARSE | 45 | 57 |

**한계**: v1/v2 유니버스 교집합 23/200 — prod 유니버스는 최근데이터순이라 구성이 바뀐다. 델타는 '같은 종목 개선'이 아니다.

## 판정

재빌드로 클래스가 바뀐 피처 12개(전부 상수0→희박). 그중 nnz≥1% 는 0개 — 즉 재빌드만으로는 실질 부활이 거의 없다. 남은 상수0 12개는 소스 부재다.

## 상수0 → 희박(정보는 생겼지만 밀도가 낮음)

| feature | nnz v1 | nnz v2 |
|---|---|---|
| negative_ratio | 0.000000 | 0.000049 |
| news_count_20d | 0.000000 | 0.000195 |
| news_count_5d | 0.000000 | 0.000195 |
| positive_ratio | 0.000000 | 0.000146 |
| sentiment_avg | 0.000000 | 0.000195 |
| sentiment_avg_20d | 0.000000 | 0.000195 |
| sentiment_avg_5d | 0.000000 | 0.000195 |
| sentiment_avg_db | 0.000000 | 0.000130 |
| sentiment_momentum | 0.000000 | 0.000097 |
| sentiment_trend | 0.000000 | 0.000049 |
| sentiment_volatility | 0.000000 | 0.000049 |
| theme_exposure_5d | 0.000000 | 0.000227 |

## 여전히 상수 0 (소스 부재)

- authenticity_avg
- credit_balance_change
- days_to_cover
- etf_flow_5d
- event_patent_5d
- event_recall_5d
- institution_ownership_pct
- margin_balance_change
- sector_count
- sector_momentum
- short_interest_ratio
- value_ncav

## 실질 부활(nnz≥1%)

- 없음
