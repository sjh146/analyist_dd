# macro_indicators 백필 검증 (매크로 피처 부활)

작성: 2026-09-24 KST · 스크립트: `scripts/macro_backfill.py` · 실행 위치: `stock_xgboost_ml` 컨테이너(`python /app/scripts/macro_backfill.py`)

## 실행 전 (실측)

```
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc "SELECT count(*) FROM macro_indicators;"
0
```
피처 리더(`app.feature_engine.macro_features.MacroFeatures.get_macro_from_db`) → **0 / 13 비영**

## 채운 데이터 (실측: 스크립트 출력 + SQL)

```
[macro_backfill] window 2023-09-19 .. 2026-09-23 | db stock_user@postgres:5432/stock_trading
[macro_backfill] FRED DEXKOUS: 751 obs / DEXJPUS: 751 / DEXCHUS: 751 / DCOILWTICO: 744
[macro_backfill]   CNY/KRW 환율   751 obs  2023-09-19 .. 2026-09-18
[macro_backfill]   JPY/KRW 환율   751 obs  2023-09-19 .. 2026-09-18
[macro_backfill]   USD/KRW 환율   751 obs  2023-09-19 .. 2026-09-18
[macro_backfill]   WTI 유가       744 obs  2023-09-19 .. 2026-09-15
[macro_backfill] upserted 2997 rows (idempotent on UNIQUE(indicator_name, date))
[macro_backfill] table now: 2997 rows / 4 indicator names
```

| indicator_name | rows | range | unit | min | max |
|---|---|---|---|---|---|
| CNY/KRW 환율 | 751 | 2023-09-19 .. 2026-09-18 | KRW | 178.546 | 229.985 |
| JPY/KRW 환율 | 751 | 2023-09-19 .. 2026-09-18 | KRW | 855.389 | 1015.060 |
| USD/KRW 환율 | 751 | 2023-09-19 .. 2026-09-18 | KRW | 1288.450 | 1555.960 |
| WTI 유가 | 744 | 2023-09-19 .. 2026-09-15 | USD/barrel | 55.440 | 114.580 |

멱등성: 2회 연속 실행 → `upserted 2997 rows` / `table now: 2997 rows` (중복 없음).

## 피처 리더 before/after (실측 그대로)

before: 0 / 13 비영 →
```
{"interest_rate": 0.0, ..., "fx_usd_krw": 0.0, "oil_wti": 0.0, ...}   # 전부 0
```
after:
```
{"interest_rate": 0.0, "interest_rate_change_1m": 0.0, "interest_rate_change_3m": 0.0,
 "fx_usd_krw": 1387.97, "fx_change_1m": 1.9187276038301202, "fx_change_3m": 1.9187276038301202,
 "oil_wti": 107.02, "oil_change_1m": 3.3310804286955706, "oil_change_3m": 3.3310804286955706,
 "cpi_yoy": 0.0, "ppi_yoy": 0.0, "yield_spread": 0.0, "credit_spread": 0.0}
AFTER nonzero: 6 / 13
```

부수 효과 (같은 테이블을 읽는 `alt_features.cross_asset_correlation('005930', '2026-09-23', 20)`):
```
{"fx_corr_20d": 0.12925889707013413, "oil_corr_20d": 0.4305600471730986, "rate_corr_20d": 0.0}
```
→ fx_corr_20d / oil_corr_20d 2개가 추가로 살아났고, rate_corr_20d 는 '기준금리' 입력이 없어 여전히 0.

## 소스 접근 실측 결과

| 소스 | 결과 |
|---|---|
| FRED CSV `fredgraph.csv?id=DEXKOUS/DEXJPUS/DEXCHUS/DCOILWTICO` | HTTP 200, 744~751행 (사용) |
| ECOS (`ECOS_API_KEY`) | 키 없음(len=0) → `/api/{키}/...` HTTP 404, 키 자리만 다른 URL형(`/api/StatisticSearch//json/...`)은 `{"RESULT":{"CODE":"INFO-100","MESSAGE":"인증키가 유효하지 않습니다..."}}` |
| FRED `CPALTT01KRM659N` (한국 CPI YoY, 월) | HTTP 200 이지만 최신 관측 2023-11 → 현재 피처용으로 부적합 |
| FRED `IRLTLT01KRM156N` | 10년물(월) — '국고채3년' 대체 불가(만기 불일치) |
| 네이버 `finance.naver.com/marketindex/interestDailyQuote.naver` (국고채3년/회사채3년) | HTTP 410 Gone |
| 네이버 `m.stock.naver.com/api/marketindex/*` | HTTP 404 (namespace 폐지), `api.stock.naver.com/marketindex/exchange/USD` → 409 |
| 네이버 `api.stock.naver.com/index/IRR_GOVT3Y/basic` | HTTP 409 `{"code":"StockConflict","message":"지원하지 않는 지수입니다. symbol=IRR_GOVT3Y"}` |
| ECOS 데모키 `sample` | HTTP 404 / 503 Service Unavailable (차단) |
| Yahoo `KR3YT=RR` | 404 (한국 국채 수익률 심볼 없음) |
| stooq CSV | 봇 차단(JS 챌린지 HTML) |
| KOFIA 채권정보센터 `selectBondStdRate.do` | HTTP 404 |

## 재실행 방법

```
docker exec stock_xgboost_ml python /app/scripts/macro_backfill.py --years 3        # 수집+업서트(멱등)
docker exec stock_xgboost_ml python /app/scripts/macro_backfill.py --verify         # 저장 현황만
docker exec stock_xgboost_ml python /app/scripts/macro_backfill.py --only "USD/KRW 환율","WTI 유가" --dry-run
```
ECOS 키 재발급 후(환경변수로만 주입, .env 수정은 스크립트가 하지 않음):
```
docker exec -e ECOS_API_KEY=<new> stock_xgboost_ml python /app/scripts/macro_backfill.py --source ecos --only "기준금리,국고채3년,회사채3년,CPI,PPI"
```

## 되돌리기

```
docker exec stock_postgres psql -U stock_user -d stock_trading -c "DELETE FROM macro_indicators WHERE indicator_name IN ('USD/KRW 환율','JPY/KRW 환율','CNY/KRW 환율','WTI 유가');"
rm -f scripts/macro_backfill.py reports/macro_backfill_verification.md
```
(테이블은 백필 전 0행이었으므로 위 DELETE 로 원상복구된다.)
