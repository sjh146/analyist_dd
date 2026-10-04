조사 완료. 아래는 gstack /investigate 형식의 보고서입니다 (파일 수정 없음, 전부 읽기 전용).

---

# /investigate 보고서: Phase 2 챌린저 재학습 피처 빌드 1.25 pair/s 병목

## 0. 관측된 증상 (사실)

- 12,101 페어(200종목×90일) 빌드가 **1.25 pair/s**(=0.80초/페어)로 실측됨 — scripts/full_pipeline_dd.sh:308-311
- 스크립트 주석이 전제한 실측 기준 **0.45초/페어(=2.2 pair/s)** — scripts/full_pipeline_dd.sh:313 (작성 시점 2026-09-24 커밋 042123a, git log 확인)
- retrain_champion.py:227의 docstring에도 같은 실측(1.26페어/s)이 기록되어 있음
- 09-24·25·28·29·30 매일 exit=124 → 5일간 승격 후보 0건

## 1. 재현/증거

코드 경로: `docker exec ... python -m app.training.retrain_champion --days 90 --stock-limit 200` → `main()`이 **단일 psycopg2 연결** 생성(retrain_champion.py:78-87, 239) → `FeaturePipeline(pg_conn=pg)` → `build_training_features()`가 페어마다 `build_features()` 호출(feature_pipeline.py:429-442). 시세(market_data)만 사전 일괄 적재(feature_pipeline.py:356-390)되어 있고 **나머지 전부 페어 단위 질의**다.

## 2. 원인 후보

| 후보 | 확신도 | 근거 |
|---|---|---|
| A. **페어당 ~50회 DB 왕복 N+1 구조** | [높음] — 코드 사실 (아래 표) | 전수 계수 결과 |
| B. 그중 **3개 중(重)쿼리가 페어마다 실행** | [높음] — 코드 사실 / 시간 점유는 [중간] | stock_prices 전체 스캔 2개 + financial_statements 상관 서브쿼리 스캔 1개 |
| C. 경쟁(다른 컨테이너 부하, load1 4.6) | 원인 여부 [낮음/추측] — 배제 아직 안 됨 | 사실인 것: 지금 load1=5.41, 4코어(uptime 실측). 인과는 미확인 |
| D. 데이터 성장(테이블 증가로 스캔 느려짐) | [낮음/추측] | stock_prices/news_events/sns_posts가 매일 증가 → B의 스캔 비용 증가 |

**주요 반증 포인트**: 0.45초→0.80초 격차가 코드 성장 때문이라는 설은 약하다 — news_event(08-14 커밋 496eb82), vector·company 백분위 부활(09-24 4ec4cbd) 모두 0.45초 기준 작성 시점(09-24)에 이미 들어와 있었다 [중간].

## 3. ① 페어당 질의 전수표

호스트 경로 `services/xgboost-ml/...` = 컨테이너 `/app/...` (docker-compose.yml:156 마운트). 페어 = (종목×날짜).

### 3a. PostgreSQL — 페어당 실행되는 것 (계수 합계 ≈ 41~46회)

| # | 질의 | file:line | 페어당 | 비고 |
|---|---|---|---|---|
| 1 | foreign_institutional(수급) | market_features.py:200 | 1 | 종목별 프리로드 가능 |
| 2 | futures_options(파생) | market_features.py:238 | 1 | **날짜 무관·전페어 동일값** + pipeline:949의 as-of basis가 **덮어씀** → 중복(제거 가능) |
| 3 | stocks.market_cap | factor_features.py:50 | 1 | 종목당 1회면 충분 |
| 4 | financial_statements LIMIT 4 | factor_features.py:65 | 1 | date 필터 없음(룩어헤드 별건) |
| 5 | 연간 재무(12월 결산) | factor_features.py:111 | 1 | 〃 |
| 6 | current_items | factor_features.py:165 | 0–1 | 컬럼 프로브는 프로세스 캐시(146-156) |
| 7 | 이익 이력 LIMIT 8 | factor_features.py:189 | 1 | 종목당 1회면 충분 |
| 8 | 재무 as-of LIMIT 2 | company_features.py:48 | 1 | 진짜 페어 단위 |
| 9 | 자기 per/pbr/섹터 | company_features.py:133 | 1 | |
| 10 | **피어 per/pbr — 상관 서브쿼리 전테이블 스캔** | company_features.py:170-201 | 1 | **중쿼리①** — `report_date = (SELECT MAX(...))`가 행마다 실행 |
| 11 | stock_sentiment LIMIT 20 | sentiment_features.py:125 | 1 | date 필터 없음(룩어헤드 별건) |
| 12 | disclosures EXISTS 프로브 | sentiment_features.py:91 | **1** | information_schema를 **페어마다** 조회(캐시 없음!) |
| 13 | disclosures count | sentiment_features.py:100 | 0–1 | |
| 14-15 | news_events 최근/과거 통계 | news_event_features.py:131,149 | 2 | |
| 16 | novelty/importance | news_event_features.py:184 | 0–1 | recent>0일 때만 |
| 17 | 이벤트 카운트 5d | news_event_features.py:264 | 1 | |
| 18 | 테마 노출 5d | news_event_features.py:309 | 1 | |
| 19 | stock_sentiment(파이프라인) | feature_pipeline.py:1037 | 1 | #11과 중복 조회 |
| 20 | F-Score | scorer.py:28 | 1 | |
| 21 | 섹터 + 섹터 종가 | feature_pipeline.py:559,563 | 1–2 | |
| 22 | **market_breadth — stock_prices 14일 윈도우 스캔** | feature_pipeline.py:598-610 | 1 | **중쿼리②** — 날짜만의 함수인데 페어마다 실행 |
| 23 | program_trading_ratio | feature_pipeline.py:653 | 1 | 날짜 전용(캐시 가능) |
| 24 | etf_flow_5d | feature_pipeline.py:673 | 1 | 날짜 전용 |
| 25-27 | ownership ×3 | feature_pipeline.py:693,712,731 | 3 | |
| 28-29 | short_interest ×2 | feature_pipeline.py:751,770 | 2 | |
| 30 | margin_balance | feature_pipeline.py:796 | 1 | |
| 31 | credit_balance | feature_pipeline.py:815 | 1 | |
| 32 | krx_short_selling | feature_pipeline.py:834 | 1 | |
| 33 | krx_total_trading_value | feature_pipeline.py:853 | 1 | 날짜 전용 |
| 34 | **krx ADR — stock_prices 14일 윈도우 스캔** | feature_pipeline.py:871-885 | 1 | **중쿼리③** — 날짜 전용인데 페어마다 |
| 35 | futures_premium | feature_pipeline.py:904 | 1 | 날짜 전용 |
| 36 | derivatives_volume | feature_pipeline.py:924 | 1 | 날짜 전용 |
| 37 | basis(futures_options LIMIT 6) | feature_pipeline.py:949 | 1 | 날짜 전용(as-of) |
| 38 | economic_events 7d | feature_pipeline.py:1066 | 1 | 날짜 전용 |
| 39 | **pgvector 코사인 유사도** | vector_features.py:125 | 1 | **날짜 무관** — 임베딩은 종목별 정적 → 종목당 1회면 충분(현재 60회/종목) |
| 40-49 | 유사종목 return_5d **×10** | vector_features.py:167 | **10** | 피처 내부 N+1 — ANY() 배치 1회로 압축 가능 |
| — | SNS posts/features/price | sns_feature_bundle.py:153,166,181 | 0(종목당 3회) | 프리페치 캐시 선례(145-204) ✓ |
| — | macro_indicators | macro_features.py:174 | 0(5분당 1회) | TTL 300 캐시 ✓ |
| — | 시장수익률 | feature_pipeline.py:1117 | 0(날짜당 1회) | 캐시 ✓ |

추가 왕복: 테이블이 **없으면**(ownership/short_interest/margin/credit/etf_flow 등) 각 질의가 예외+`rollback()` 왕복 2회씩 가산 — 미확인 [추측].

### 3b. Neo4j — 페어당 4~5회 (그래프 피처)

| 질의 | file:line | 비고 |
|---|---|---|
| 섹터 | graph_features.py:84 | 종목당 1회면 충분 |
| 테마(멤버+히스토리) | graph_features.py:105 | 〃 (momentum은 파이썬 계산, :130) |
| 트윈 | graph_features.py:171 | 〃 |
| 사이클(date 노드) + 폴백 | graph_features.py:198,209 | **날짜 전용** → 날짜당 1회 |
| — | graph_features.py:42 | `_rows`마다 **새 세션** 생성/파괴(4~5회/페어) |

## 4. ② 줄일 수 있는 후보 (우선순위 = 효과/위험)

| 우선 | 후보 | 기대 효과 | as-of 정합성 | 위험 |
|---|---|---|---|---|
| **P1** | **날짜 전용 질의 12종(#2,23,24,33,34,35,36,37,38,22, + Neo4j 사이클)을 날짜별 캐시로 1회화** — `_get_market_return`(feature_pipeline.py:1104-1140)이 **이미 같은 패턴** | 중쿼리②③ 제거: stock_prices 전체 스캔 2회×12,101 → 날짜당 1회 | **안전 [높음]** — 날짜만의 함수라 값 동일 | 낮음 |
| **P2** | **vector 11회 → 2회**: 코사인 질의(#39)는 종목당 1회 캐시, return_5d 10개(#40-49)는 `stock_code = ANY(%s)` 배치 1회 | 12,101×10 왕복 제거 | **안전 [높음]** — `trade_date <= date` 필터 그대로 유지(vector_features.py:167-174) | 낮음 |
| **P3** | **재무 질의 7종 통합 종목별 프리로드** (factor:65/111/165/189, company:48/133, scorer:28) — build_training_features가 market_data에 쓴 방식(feature_pipeline.py:356-390) 복제 | 페어당 ~7회 → 0 | 주의 [중간]: factor:65·scorer:28은 **현재 date 필터가 없음** — 프리로드에 as-of를 넣으면 값이 바뀜(train/infer 동일 파이프라인이라 양쪽 일관성은 유지되나 기존 챔피언 대비 피처 값 변경 = 별도 판단 항목) | 중간 |
| **P4** | **중쿼리① 피어 백분위(company:170-201) → 날짜별 피어 분포 1회 계산 + 파이썬 랭킹** | 상관 서브쿼리 전스캔 제거 | **안전 [높음]** — `report_date <= date` 조건 유지하면 동일 | 낮음 |
| **P5** | **종목별 시계열 프리로드**: 수급(#1), ownership×3, short_interest×2, margin, credit, krx_short_selling, stock_sentiment×2(#11,#19), news_events 4-5종 — SNS 번들(sns_feature_bundle.py:145-204)이 선례 | 페어당 ~14회 → 0 | **안전 [높음]** — 전부 `<= date` 슬라이스로 파이썬 계산. 단 #11은 현재 as-of가 아님 → 그대로 배치하면 누수 유지, 고치면 값 변경(별도 결정) | 낮음~중간 |
| **P6** | disclosures EXISTS 프로브(#12) 프로세스 1회 캐시 — factor의 `_FIN_COL_CACHE`(factor_features.py:146-156) 패턴 복제 + 중복 futures_options(#2) 제거 | 12,101×2 왕복 제거 | **안전 [높음]** — #2는 어차피 덮어써짐 | 낮음 |
| P7 | Neo4j 세션 종목/날짜당 1회로 (graph_features.py:42) | 왕복 4~5→1 | 안전 [높음] | 낮음 |
| P8 | 체크포인트 저장(feature_pipeline.py:513)이 500페어마다 **전체 누적 DataFrame 재직렬화** | 후반부 수백ms/회 | 무관 | 낮음 |
| — | 단일 연결 병렬화(스레드) | 큼 | — | **높음(재설계)** — 권장 안 함 |

**예상치(추측)**: P1~P7 적용 시 페어당 왕복 ~45회 → ~5회 미만, 중쿼리 3개 제거로 0.80초 → 0.15~0.3초/페어(2~4배 이상) [중간]. 12,101 페어 ≈ 30~60분이면 12600s 예산에 여유.

## 5. ③ 경쟁(load) 가설 반증 — 측정 명령

**1) 핵심 반증 — 조용한 시간 단독 재실측 (같은 구간 고정):**
```bash
# 새벽(다른 크론이 없는 시간)에 동일 파라미터로:
docker exec stock_xgboost_ml sh -c "cd /app && time python -m app.training.retrain_champion \
  --days 90 --stock-limit 200 --end-date 2026-09-30 --out-dir /tmp/cand_probe"
# 'Build progress: ... N.NN pair/s' 줄 비교: 조용해도 ≈1.25 → 경쟁 가설 기각(코드 구조가 원인)
#                                      조용할 때 ≈2.2+ → 경쟁이 격차(0.45→0.80)의 주원인
```

**2) DB 대기 vs CPU 분리 (병목 종류 판별):**
```bash
docker exec stock_xgboost_ml sh -c "cd /app && python -m cProfile -s cumtime -m app.training.retrain_champion \
  --days 10 --stock-limit 30 --end-date 2026-09-30 --out-dir /tmp/cand_probe" 2>&1 | head -60
# psycopg2 cursor.execute 계열 cumtime 합 ≈ wall 시간 → 왕복/DB 대기 병목
# numpy/pandas/TechnicalIndicator 합이 크면 → CPU 경쟁(4코어 load 5.4) 영향 실재
```

**3) 중쿼리 3개의 단독 비용 (postgres CPU 하한):**
```bash
docker exec stock_postgres psql -U stock_user -d stock_trading -c "\timing on" \
  -c "EXPLAIN ANALYZE SELECT COUNT(*) FILTER (WHERE close_price > prev_close), COUNT(*) FROM (SELECT trade_date, close_price, LAG(close_price) OVER (PARTITION BY stock_code ORDER BY trade_date) AS prev_close FROM stock_prices WHERE trade_date <= '2026-09-30' AND trade_date >= '2026-09-30'::date - 14) t WHERE trade_date = '2026-09-30' AND prev_close IS NOT NULL"   # feature_pipeline.py:598-610
  # 같은 방식으로 krx ADR(feature_pipeline.py:871-885), 피어 백분위(company_features.py:170-201)도
# 한 번에 수백 ms면 → 조용한 DB에서도 코드가 원인임이 확정
```

**4) 실행 중 경쟁 증거:**
```bash
docker exec stock_postgres psql -U stock_user -d stock_trading -c \
 "SELECT pid, state, wait_event_type, wait_event, left(query,60) FROM pg_stat_activity \
  WHERE datname='stock_trading' AND pid <> pg_backend_pid()"
docker stats --no-stream stock_postgres stock_neo4j stock_xgboost_ml   # CPU 점유 배분
```

**5) 시간 점유 쿼리 (확장 있으면):**
```sql
SELECT left(query,60), calls, round(mean_exec_time::numeric,1), round(total_exec_time::numeric/1000,1)
FROM pg_stat_statements
WHERE query ILIKE '%stock_prices%' OR query ILIKE '%financial_statements%' OR query ILIKE '%stock_vectors%'
ORDER BY total_exec_time DESC LIMIT 15;
```

**6) 존재하지 않는 테이블(예외 왕복) + 인덱스 확인:**
```sql
SELECT table_name FROM information_schema.tables WHERE table_name IN
 ('ownership','short_interest','margin_balance','credit_balance','etf_flow','disclosures','krx_program_trading','krx_short_selling','krx_trading','krx_derivatives','futures_options','economic_events','stock_vectors','foreign_institutional','stock_sentiment');
SELECT indexname, indexdef FROM pg_indexes WHERE tablename IN ('stock_prices','market_data','financial_statements');
```

## 6. 최소 결론

1. **1.25 pair/s의 구조적 원인은 페어당 ~45~50회 DB 왕복(N+1) + 페어마다 실행되는 전테이블 스캔 3개다** [높음 — 코드 전수 계수로 확정된 사실]. market_data와 SNS는 이미 배치/캐시가 되어 있는데(feature_pipeline.py:356-390, sns_feature_bundle.py:145-204), 그 밖의 모든 리더가 페어 단위 질의를 한다.
2. **load1 4.6~5.4(4코어) 경쟁은 실재하지만**(uptime 실측, 사실) 원인인지는 미확정 [추측] — §5의 새벽 단독 실행으로 반증 가능. 다만 0.45→0.80초 격차의 유력 후보는 경쟁·데이터 성장·측정 조건이며, "코드 성장"은 아니다(기준 작성 09-24 시점에 이미 오늘과 같은 질의 구성) [중간].
3. **최우선 조치 후보는 P1(날짜 전용 질의 캐시) > P2(벡터 배치) > P4(피어 스캔) > P3/P5(프리로드)** — 모두 as-of 정합을 유지할 수 있고, `_get_market_return`(feature_pipeline.py:1104-1140)과 SNS 번들이 이미 검증된 패턴을 제공한다 [높음]. 단 P3/P5에서 현재 as-of가 깨져 있는 질의(factor_features.py:65, sentiment_features.py:125, scorer.py:28)를 건드릴 때는 피처 값이 바뀌므로 별도 트랙으로 다뤄야 한다 [높음].

**파일 수정 없음** — 위 §5의 측정 명령 1)~6)은 실행 전 승인 대기(특히 docker exec 계열). 원하시면 조용한 시간대에 명령 1)을 예약 실행해 반증 결과를 확인해 드리겠습니다.
