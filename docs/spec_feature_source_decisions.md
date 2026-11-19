# 죽은 피처 처리 결정 — 수급·공매도·신용 계열 (2026-11-19)

## 실측: 원천이 존재하는가

| 피처 | 피처 상태 | 원천 테이블 | 실측 | 판정 |
|---|---|---|---|---|
| `foreign_net_buy`/`_5d` | **ALIVE**(nnz 25%) | `foreign_institutional` | 196,235행 · 2025-08~2026-11 · 패널 94/200 | 유지 |
| `institution_net_buy`/`_5d` | **ALIVE**(nnz 20%) | 〃 | 〃 | 유지 |
| `short_selling_ratio` | DEAD_SPARSE(nnz 3.4%) | `krx_short_selling` | 24,556행·308종목·패널 26/200 | **커버리지 확대**(백필 진행) |
| `foreign_ownership_pct`/`retail_ownership_pct` | DEAD_SPARSE(nnz 4.5%) | `ownership` | 6,439행·2026-09-23~ / 패널 87/200 | 커버리지 확대(수집 창 연장) |
| `short_interest_ratio` | DEAD_CONST_ZERO | **없음** | KIS 공매도 **잔고** TR 6종 프로브 → 전부 HTTP 404. KRX OpenAPI 카탈로그에도 없음 | **폐기** |
| `days_to_cover` | DEAD_CONST_ZERO | **없음** | 위와 동일(잔고 필요) | **폐기** |
| `institution_ownership_pct` | DEAD_CONST_ZERO | `ownership` | 6,439행 전부 **NULL** | **폐기**(원천 미제공) |
| `credit_balance_change` | DEAD_CONST_ZERO | **없음** | 신용잔고 테이블 없음(KIS 미제공) | **폐기** |
| `margin_balance_change` | DEAD_CONST_ZERO | **없음** | 〃 | **폐기** |

## 폐기 사유 (코드 근거)

`scripts/kis_short_selling_backfill.py` docstring 실측:
- KIS `FHPST04830000` = **공매도 체결**(일별 거래)만 제공. 잔고 계열 후보 6경로
  (`daily-short-sale-balance`, `inquire-short-sale-balance`, `short-sale-balance-trend`,
  `short-sale`, `ranking/short-sale-balance`, `daily-loan-balance`) → **전부 404**.
- KRX OpenAPI 서비스 목록(주식 카테고리)에 공매도 서비스 없음.
→ `short_interest_ratio`(잔고/발행주식), `days_to_cover`(잔고/평균거래량)는 **데이터 자체가 없다.**

`feature_pipeline.py` 는 존재하지 않는 테이블(`short_interest`, `stock_prices`,
`margin_balance`, `credit_balance`)을 조회하고 예외 시 `0.0` 으로 채운다 → 상수 0의 원인.

## 조치

1. **폐기 5개**: `short_interest_ratio`·`days_to_cover`·`institution_ownership_pct`·
   `credit_balance_change`·`margin_balance_change` — 패널에서 컬럼을 제거하거나 NaN 유지.
   (0으로 두면 "실제 0"과 구분되지 않아 모델이 거짓 정보를 학습한다)
2. **커버리지 확대 1개**: `short_selling_ratio` — KIS 체결 데이터로 전 종목 백필
   (`--limit 0 --start 20250804`), 커버리지 26/200 → 190+ 목표.
3. **커버리지 확대 2개**: `foreign_ownership_pct`/`retail_ownership_pct` — `ownership` 수집 창이
   2026-09-23부터라 패널 앞구간이 비어 있다 → 수집기 창 연장(별도 작업).

## 측정 규칙 (재발 방지)

피처를 "살릴 수 있는지" 판단할 때 **원천 존재 여부를 먼저 확인**한다:
```
SELECT count(*) FILTER (WHERE <col> IS NOT NULL), count(*) FROM <table>;
SELECT count(DISTINCT stock_code) FROM <table> WHERE stock_code = ANY(<패널코드>);
```
→ 전부 NULL/0 이면 그 피처는 **빌더 문제가 아니라 데이터 부재**다. 빌더를 고쳐도 살아나지 않는다.
