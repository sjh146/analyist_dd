# 인계 패치 — `relative_strength` (feature_pipeline.py, 수정 금지 파일)

작성: 2026-09-24 / 담당: 가격·유사도·베이즈·밸류에이션 백분위 계열 자식 에이전트
대상 파일: `services/xgboost-ml/app/feature_engine/feature_pipeline.py`
(지시에 따라 이 자식은 `feature_pipeline.py` 를 **수정하지 않았다**.)

## 1. 원인 (실측)

`_build_advanced_features()` (약 435~441행):

```python
        # 2. relative_strength: stock_return / market_return
        features["relative_strength"] = 0.0
        if valid_close and len(close) >= 2:
            stock_ret = close[-1] / close[-2] - 1 if close[-2] != 0 else 0.0
            # TODO: fetch KOSPI index return from market_index table for accurate market_return
            market_return = 0.0
            features["relative_strength"] = float(stock_ret / market_return) if market_return != 0 else 0.0
```

`market_return = 0.0` 이 하드코딩되어 있어 마지막 식이 **항상 0.0** 이다.
검증(2026-09-24, `docker exec stock_xgboost_ml python scripts/verify_feature_revival.py`)에서
3종목 × 3날짜 = 9행 전부 `relative_strength = 0`, `n_distinct = 1` → DEAD.

또한 리포지토리에 지수(KOSPI) 시계열 테이블이 **없다**:
`information_schema.tables` 전수 조회 결과 `market_index`·`index_data` 류 테이블 부재
(`us_market_data`, `macro_indicators`, `krx_derivatives` 등만 존재).
따라서 지수 대신 **시세 기반 동일가중 시장수익률 프록시**를 쓴다.

## 2. 프록시 타당성 실측

전 종목 동일가중 1일 수익률(`market_data`, 거래정지 행 제외)은 날짜별로 값이 살아 있다:

```
2026-09-23 | +0.00399 | 3317종목
2026-09-22 | -0.00148 | 3318
2026-09-21 | -0.00090 | 3317
2026-09-18 | +0.00637 | 3312
2026-09-17 | +0.00594 | 3314
2026-09-16 | +0.00032 | 3320
```

## 3. 적용할 패치 (unified diff)

```diff
--- a/services/xgboost-ml/app/feature_engine/feature_pipeline.py
+++ b/services/xgboost-ml/app/feature_engine/feature_pipeline.py
@@ -432,11 +432,42 @@
         # 2. relative_strength: stock_return / market_return
         features["relative_strength"] = 0.0
         if valid_close and len(close) >= 2:
             stock_ret = close[-1] / close[-2] - 1 if close[-2] != 0 else 0.0
-            # TODO: fetch KOSPI index return from market_index table for accurate market_return
-            market_return = 0.0
-            features["relative_strength"] = float(stock_ret / market_return) if market_return != 0 else 0.0
+            # 지수 테이블이 없으므로 전종목 동일가중 평균 1일 수익률을 시장수익률 프록시로 쓴다.
+            market_return = self._get_market_return(date)
+            if market_return is not None:
+                # 비율(stock/market) 대신 초과수익률(차) — 시장수익률이 0 근처일 때
+                # 분모 폭발을 막고 부호 해석도 그대로 유지된다.
+                features["relative_strength"] = float(stock_ret - market_return)
+
+    def _get_market_return(self, date: str):
+        """해당 날짜의 전종목 동일가중 평균 1일 수익률 (날짜별 1회 캐시).
+
+        market_data 는 (stock_code, trade_date) 기준이다. 10일 창 안에서만 LAG 를
+        계산한 뒤 마지막 행들의 평균을 낸다(창 전체 스캔이지만 인덱스로 처리되고,
+        날짜별 1회만 조회한다).
+        """
+        key = ("market_return", date)
+        if key in self._cache:
+            return self._cache[key]
+        if self.pg_conn is None:
+            return None
+        val = None
+        try:
+            cur = self.pg_conn.cursor()
+            cur.execute(f"""
+                SELECT AVG(r) FROM (
+                    SELECT (close_price / NULLIF(LAG(close_price) OVER (
+                                PARTITION BY stock_code ORDER BY trade_date), 0)) - 1 AS r
+                    FROM market_data
+                    WHERE trade_date BETWEEN %s::date - INTERVAL '10 days' AND %s::date
+                      AND {MARKET_DATA_VALID}
+                ) t
+                WHERE r IS NOT NULL
+            """, (date, date))
+            row = cur.fetchone()
+            cur.close()
+            if row and row[0] is not None:
+                val = float(row[0])
+        except Exception:
+            logger.debug("market_return unavailable; relative_strength stays 0.0")
+            self.pg_conn.rollback()
+        self._cache[key] = val
+        return val
```

`{MARKET_DATA_VALID}` 는 이 파일 상단에서 이미 import 되어 있다
(`from app.feature_engine.market_data_filter import MARKET_DATA_VALID`).
`self._cache` 는 `build_features` 가 쓰는 기존 캐시 dict 이며 `clear_cache()` 로 비운다.

## 4. 함께 남기는 것 — `stocks.sector` 미채움 (별건)

`per_percentile`/`pbr_percentile` 을 섹터 상대 백분위로 계산하려면 `stocks.sector` 가
필요하지만, 실측 결과 **일반 주식에는 섹터가 하나도 없다**:

```
instrument_type | total | sector_filled
STOCK           |  2796 |      0
ETF             |  1175 |   1175
ETN             |   369 |    369
```

`industry` 도 0건. 리포지토리가 승인한 KRX OpenAPI 서비스는 일별매매정보
(`sto/stk_bydd_trd`, `sto/ksq_bydd_trd`)뿐이고 업종분류는 차단 이력이 있는
스크래핑 엔드포인트(`comm/bldAttendant/getJsonData.cmd`)로 접근해야 하므로
**IP 보호 정책상 호출하지 않았다**(`scripts/krx_daily.py` 헤더 원칙).
무료 대안인 DART `company.json`(induty_code)은 종목당 1콜 × 2796 이 필요하고
현재 돌고 있는 `dart_financial_backfill.py` 와 레이트리밋을 다투므로 이번 범위에서 제외했다.

대신 `company_features.get_percentile_features()` 를 **섹터가 없으면 시장 전체 피어 집합**으로
폴백하도록 고쳤다(코드 변경 완료). 나중에 `stocks.sector` 가 채워지면 같은 코드가
자동으로 섹터 상대 백분위로 전환된다(추가 수정 불필요).

## 5. 부수 인계 — 파이프라인에 `date` 를 넘기면 좋아지는 3곳 (선택)

이번에 담당 모듈들이 `date` 인자를 지원하도록 만들어 두었지만, 호출부(`feature_pipeline.py`)가
date 를 넘기지 않아 기본 동작(최신 데이터)으로 돈다. 각각 1줄 수정이다.

```diff
@@ build_features (약 127행)
-        features.update(self.factors.get_all_features(stock_code, market_df, self.pg_conn))
+        features.update(self.factors.get_all_features(stock_code, market_df, self.pg_conn, date=date))
```
(※ `factor_features.get_all_factors` 는 이 자식 담당이 아니므로 시그니처 확인 필요)

```diff
@@ build_features (약 130행)
-        features.update(self.company.get_all_features(stock_code, self.pg_conn))
+        features.update(self.company.get_all_features(stock_code, self.pg_conn, date=date))
```
→ `per_percentile`/`pbr_percentile` 이 `report_date <= date` 재무제표만 쓰게 되어
   백테스트 패널의 미래참조가 사라진다(현재는 date=None → 최신 재무제표 = 룩어헤드).

```diff
@@ build_features (약 151행)
-        features.update(self.vector.get_vector_features_from_db(stock_code, self.pg_conn))
+        features.update(self.vector.get_vector_features_from_db(stock_code, self.pg_conn, date=date))
```
→ 유사 종목의 5일 수익률(`similar_stocks_return_avg/std`)을 date 기준으로 계산.

## 6. 부수 인계 — `momentum_3_12m` 을 전 구간에서 살리려면

`factor_features._momentum_factors` 는 `len(close) >= 252` 를 요구하는데,
파이프라인이 시세를 두 곳에서 잘라 준다:

```diff
@@ build_training_features (약 325행)
-   lookback_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
+   # 365일 ≈ 247~255 거래일이라 252 조건에 걸린다 → 520일로 확대
+   lookback_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=520)).strftime("%Y-%m-%d")

@@ build_features (약 88행)
-                       LIMIT 250
+                       LIMIT 400
```

KRX 백필(2025-06-16~)이 이미 들어가 있으므로 위 두 줄만 고치면 패널 전 구간에서
`momentum_3_12m` 이 살아난다(현재는 패널 시작일이 lookback 경계보다 앞설 때만 산다).

