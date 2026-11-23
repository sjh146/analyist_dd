# 죽은 피처 전수 조사 · 그룹별 되살리기

대상: 173피처 중 상수 0 이거나 상수값이라 트리가 쓸 수 없는 것들.
"왜 피처가 죽어 있나"에 **그룹별 원인을 특정**하고, 되살린 뒤 **피처 리더 호출로 실측 검증**까지 가는 절차.

## 1. 전수 조사 2단계 (호스트에서)

```
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup ASC"
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT count(*) FILTER (WHERE nonzero_ratio=0) all_zero,
          count(*) FILTER (WHERE nonzero_ratio>0 AND std=0) constant,
          count(*) FILTER (WHERE nonzero_ratio>0 AND std>0) informative FROM feature_coverage"
```

3가지 죽음 모드로 분류한다: **전부 0** / **값은 있으나 `std=0`(상수)** / **명목상 살아있음**(`nonzero_ratio` 1~2% — 라이브 크론이 만든 며칠치만 있어 학습 구간 대부분이 0).
`nonzero_ratio>0` 만 보면 3번을 "살아있다"로 오판한다.

## 2. 원인 분류 — grep 으로 먼저 가른다

- **writer 코드 부재**: `grep -rln "INSERT INTO <table>" --include=*.py services/ scripts/`
  → **피처 리더만 나오고 writer 가 없으면 그 테이블은 영원히 0행**이다(수집기가 있어도 값이 저장되지 않는다).
  실측: `macro_indicators`, `sns_post_features`(리더만 존재). 이 그룹은 "수집기 실행"이 아니라 **저장 코드 신규 작성**이 일이다.
- **수집기 존재·미실행**: `services/krx-collector/app/collectors/`(short_selling·investor·program_trading·derivatives),
  `services/yfinance-collector/app/collectors/macro_collector.py`, `scripts/sns_collect_runner.py`, `services/news-analyzer/app/events/`(clusterer).
  코드는 완성돼 있는데 서비스 루프·크론에 배선되지 않아 한 번도 안 돈 경우가 가장 많다.
- **자격증명 무효**: ECOS 는 `{"RESULT":{"CODE":"INFO-100","MESSAGE":"인증키가 유효하지 않습니다"}}`(HTTP 200) 로 답한다.
  단 **키 재발급을 요청하기 전에 코드를 먼저 의심하라** — 실측에서 "키 무효"로 보이던 실패는 전부 URL·코드 문제였다:
  필수 경로 세그먼트 누락(HTTP 404 · 빈 본문), 잘못된 통계표/항목 조합(HTTP 200 + `INFO-200 해당하는 데이터가 없습니다`),
  요청 상한(1,000행)으로 **최신 구간 절단**. 검증된 코드·1콜 프로브는 `korean-market-data-apis` §12.
  KRX 계열 수집기는 pykrx 기반이라 `KRX_ID`/`KRX_PW`(무료 회원) 또는 공식 OpenAPI **서비스 이용 신청**이 있어야 돌고,
  **투자자별 수급·공매도·프로그램매매는 KRX 서비스 카탈로그에 없다** → 그 피처군은 KIS 등 다른 소스로 피벗한다
  (사용자에게 "KRX 키를 발급받으라"고 안내하면 틀린 안내다).
- **소스 커버리지가 좁아 0**(코드도 데이터도 정상인 경우): 피처가 **특정 종목군에만** 값이 있는 유형이다.
  실측: `event_*_5d` 18개가 커버리지 패널에서 전부 0 으로 찍혔지만 이벤트를 가진 종목에서는 정상값이 나왔다 —
  추출이 소수 종목만 덮었고(종목명 부분문자열 매칭이라 추출 유니버스 목록 밖 기사는 버려진다), 그 목록이 모델 학습
  유니버스와 겹치지 않아 학습 데이터에서는 통째로 0 이었다. **판정 순서: (a) 소스 테이블의 distinct 종목 수·최신일을
  센다 → (b) 그 데이터가 있는 특정 종목으로 리더를 호출해 값을 본다 → (c) 그래도 0 이면 코드를 의심한다.**
  처방은 코드가 아니라 **추출 유니버스 확대(종목 상한 인자) + 재클러스터**다. 규칙 기반 추출이면 유료 호출이 없으니
  "LLM 비용 승인"을 요구하며 막지 말고 추출이 룰 기반인지 먼저 확인한다.
- **상류 사이트 개편**: 네이버 파이낸스 SPA 전환으로 `finance.naver.com/item/board.nhn` 이 302 → `stock.naver.com/.../discussion`
  (HTML 0바이트) — 스크래퍼가 예외 없이 0건을 반환한다. 대체는 `https://m.stock.naver.com/api/...` JSON 경로.
  **0건을 "데이터 없음"으로 읽지 말고 리다이렉트 상태코드를 확인한다.**
- **부분·경과 데이터**: 테이블이 0은 아니지만 커버리지가 얇거나 오래된 경우(실측: `financial_statements` 1,318/2,770종목 ·
  최신 `report_date` 9개월 지연) → 밸류/퀄리티 16개가 상수 0. 무료 공개 API(DART) 백필 재실행이 조치.

## 3. 입력 테이블 ↔ 피처 매핑 (실측)

| 입력 테이블 | 죽는 피처 |
|---|---|
| `news_events`, `news_event_extraction` | `event_*` 18개 + `theme_*` 4개 |
| `sns_posts`, `sns_post_features` | SNS 관심도·`kalman_sentiment/attention/momentum/activity` 계열 |
| `macro_indicators` | `fx_*` 3, `oil_*` 3, `interest_rate*` 3, `cpi_yoy`, `ppi_yoy`, `yield_spread`, `credit_*` |
| `foreign_institutional` | `foreign_*` 3 + `institution_*` 3 (**리포에 writer 자체가 없음 → 수집기 신규 개발**) |
| `krx_short_selling`, `krx_program_trading`, `krx_derivatives`, `futures_options` | `short_*`, `days_to_cover`, `program_trading_ratio`, `derivatives_volume`, `futures_premium`, `basis*` |
| `financial_statements` | `value_*` 7, `quality_*` 9, `per_current`/`pbr_current`/`roe` |
| `economic_events` | `economic_event_count_7d`, `economic_event_impact` |
| `us_market_data`, `krx_trading` | 미국 시장·시장폭 피처 |
| (테이블 아님 · 코드 문제) | `bayes_*` 4 — 리더가 `fit()` 전에 호출돼 기본값 0 을 반환(로그: `BayesFactorFeatures.compute called before fit()`) |

## 4. 되살리기 전에 리더 스키마를 맞춘다

- 리더가 읽는 **테이블·컬럼·표시명**을 grep 으로 먼저 확인하고 그 형식에 정확히 맞춰 쓴다.
  실측 함정: `macro_features` 는 지표를 **한국어 표시명**으로 조회한다(`기준금리`, `국고채3년`, `회사채3년`, `USD/KRW 환율`, `WTI 유가`, `CPI`, `PPI`).
  영어 코드로 저장하면 행이 있어도 피처는 0 이다.
- 저장(백필)과 **조회(날짜 정합)** 를 한 쌍으로 계획한다. 아래 룩어헤드 함정 참조.

## 5. 함정

- **"최신 N행" 리더는 행 날짜를 보지 않는다(룩어헤드).** `macro_features._fetch_indicators` 는 지표별 최신 4행으로
  1m/3m 변화를 계산해 **모든 학습 행에 같은 값**을 붙인다 → 테이블을 채워도 상수(분기 불가)라 AUC 는 그대로지만,
  리더를 날짜 정합으로 고치는 순간 학습 데이터에 미래 정보가 들어간다. 이 계열을 되살릴 때는 조회 코드 수정을 같이 하고,
  수정 전 수치와 후 수치를 섞어 비교하지 않는다.
- **"0행 테이블을 채웠다" ≠ "피처가 살았다".** 검증은 피처 리더를 실제 호출해 **서로 다른 (종목,일) 3쌍 이상**의 값과 변동을 찍는 것.
  값이 상수면 `std` 로 확인한다.
- **before/after 는 같은 표본으로 떠라.** `feature_coverage` 는 실행할 때마다 덮어쓰므로 작업 시작 **전에**
  `\copy (SELECT * FROM feature_coverage) TO STDOUT WITH CSV HEADER` 로 스냅샷을 파일로 남기고, 되살린 뒤 **같은 `--stocks/--days`**
  (유니버스는 seed 고정이라 결정적)로 재측정한다. 표본 크기를 바꾸면(12일 vs 30일) 죽은 피처 총계가 달라져 비교가 무효다.
  표본이 다르면 부활 판정은 "그 실행에서 nonzero+std>0" 으로만 하고 총계는 비교하지 않는다.
- **컨테이너가 코드를 메모리에 들고 있다.** 바인드 마운트 편집은 **새 프로세스**(`docker exec`)에만 적용된다 —
  돌고 있는 서비스 루프(예: stock_vectorizer 의 일 1회 실행)는 옛 코드로 계속 돌아 다음 주기에 결과를 되돌린다.
  코드를 고쳤으면 담당자에게 재기동을 인계하고, 재기동 금지 조건이면 보고에 경고로 남긴다.
- 검증·피처 호출은 **그 모듈을 가진 컨테이너**에서 한다(서비스 `app/` 은 컨테이너마다 다르다).
- 되살리기는 **유료 호출 없는 것부터**: 규칙 기반 추출(키워드 택소노미 재사용), 기존 수집기 실행, 무료 공개 API 백필.
  유료 LLM 재분석은 사용자 승인 후에만.
- **되살린 데이터는 다시 죽는다** — 수집기를 크론·서비스 루프에 배선하지 않으면 다음 수집 주기부터 다시 0행이다.
  `/etc/cron.d/analyist_dd` 가 `.disabled` 인지 확인하고, 되살림 작업의 마지막 단계에 스케줄 등록을 넣는다.

## 6. 보고 형식

그룹별로 **피처 수 / 블로커 / 지금 조치 / 사용자 결정 필요** 4열로 나눠 보고하고,
사용자가 해줘야 하는 것(키 재발급, 계정 정보, 서비스 이용 승인, 유료 호출 승인)을 **별도 목록**으로 명시한다 —
그게 없으면 나머지 그룹은 진행 불가라는 사실이 보고에서 보여야 한다.

## 7. 코드 계열 죽음 카탈로그 (가격·유사도·베이즈·백분위, 2026-09-24 실측)

테이블은 멀쩡한데 코드가 0을 만드는 유형이다. 데이터를 채우기 전에 이 6개를 먼저 배제한다.

1. **지역 dict 에 없는 키** — 헬퍼가 `features.get("price", 0)` 같은 키로 나눗셈을 하는데 그 키는 **다른 dict 를 병합할 때만**
   존재하면 조건이 항상 거짓이 되어 0 이 된다(실측: `atr_pct`, `per_percentile`). 같은 함수 안에서 `features[...]` 를 조회하면
   병합 전 지역 dict 기준이라는 점을 확인한다.
2. **만들어지지 않는 컬럼을 요구** — `if "bb_middle" in df.columns` 인데 그 컬럼을 만드는 계산기(`TechnicalIndicatorCalculator`)는
   `sma_20/rsi/macd/atr` 만 만든다 → `bb_position` 영구 0. 컬럼 생성자 쪽 docstring 과 대조한다.
3. **임베딩 퇴화(유사도 계열)** — 상수 유사도(1.0 / std 0)를 보면 벡터라이저를 본다.
   진단: `SELECT count(*), count(DISTINCT embedding), count(DISTINCT md5(embedding::text)) FROM stock_vectors`.
   실측 원인: 벡터라이저 main 이 `vectorize_price_pattern(stock_code)` 처럼 **데이터 대신 코드 문자열**을 넘겨
   내부에서 항상 영벡터를 만들었다(2773행 중 서로 다른 임베딩 3개) → 재생성으로 3040개로 복구.
   재생성은 야간 스케줄과 같은 로직을 `docker exec` 로 1회 돌린다(4314종목 ≈ 6분). `similar_count` 는 `len(top_k)` 라서
   정의상 상수다 — 살리려면 임계값 기반으로 정의를 바꾸고, 임계값은 실측 유사도 분포를 보고 정한다(0.9/0.95 는 포화, 0.99 가 변별).
4. **`fit()` 없는 상태** — `compute()` 가 캐시된 사후분포를 요구하면 학습 경로에서는 영원히 0 이다. MCMC 를 핫 루프에 넣지 말고
   **선형-가우시안 상태공간 모형의 해석적(칼만) 사후**를 폴백으로 계산한다(Q/R 적률추정 → 칼만 → 평균/분산).
   이 계약을 "0.0 을 반환한다"로 고정한 테스트가 있으면 함께 갱신한다.
5. **Decimal 타입** — psycopg2 는 numeric 을 `Decimal` 로 준다. `np.linalg.norm`/`np.concatenate` 에 그대로 넣으면
   `TypeError: unsupported operand type(s) for +: 'decimal.Decimal' and 'float'` 로 **조용히 실패**하고(로그는 ERROR 1줄)
   임베딩 저장이 통째로 빠진다. 벡터라이저 입력은 `float()` 로 강제한다.
6. **`hash()` 비결정성** — 섹터 등 범주형을 `hash()` 로 원-핫 버킷팅하면 `PYTHONHASHSEED` 때문에 **실행마다 임베딩이 달라진다**.
   유사도 피처가 매일 노이즈가 되므로 `hashlib.md5` 같은 안정 해시로 바꾼다.

### 252·250 경계 (momentum_3_12m 류)

- `len(close) >= 252` 를 요구하는데 `build_training_features` 의 lookback 은 `start_date - 365일` → **캘린더 1년 = 247~255 거래일**이라
  경계에 걸린다. 게다가 단일종목 경로는 `LIMIT 250`. 백필로 이력을 늘려도 이 두 상한을 넘지 못하면 패널 앞쪽 날짜는 여전히 0 이다.
- KRX OpenAPI 백필 비용 실측: **26초/영업일**(2콜, 3s 간격 + ~2730행 upsert) → 3개월(66영업일) 33분, 1년 55분.
  `scripts/krx_daily.py --from YYYYMMDD --to YYYYMMDD` + `PROJ_DIR`/`POSTGRES_HOST=127.0.0.1`/`POSTGRES_PORT=5434`,
  호스트에 psycopg2 가 없으면 임시 venv(`uv venv` + `psycopg2-binary`)로 돌린다.
- 살아남 판정은 **실제 패널 빌드**(`feature_coverage_report.py --ignore-cache`)로 한다. 전체 이력을 로드하는 자체 검증 스크립트는
  낙관적 결과를 준다(실측: 자체 스크립트 9/9 vs 12일 패널 0).
