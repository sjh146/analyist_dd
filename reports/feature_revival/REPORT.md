# 죽은 피처 되살리기 — 가격 · 유사도 · 베이즈 · 밸류에이션 백분위 계열

작성: 2026-09-24 (KST) / 범위: `atr_pct, bb_position, relative_strength, similarity_std,
avg_similarity_top10, max_similarity, similar_count, bayes 4개, per/pbr_percentile,
momentum_3_12m`

## 0. 요약

**동일 표본(30종목 × 30일, `feature_coverage_report.py --ignore-cache --stocks 30 --days 30`, seed 0)
before/after 실측: 죽은(0) 또는 상수(std=0) 피처 79개 → 59개.**

| 피처 | before (nonzero_ratio / std) | after | 조치 |
|---|---|---|---|
| `atr_pct` | 0 / 0 | 1.0 / 2.095 | 코드 수정 (market_features) |
| `bb_position` | 0 / 0 | 1.0 / 0.304 | 코드 수정 (market_features) |
| `similarity_std` | 0 / 0 | 1.0 / 0.00253 | 임베딩 재생성 |
| `avg_similarity_top10` | 1.0 / 0 (상수 1.0) | 1.0 / 0.0247 | 임베딩 재생성 |
| `max_similarity` | 1.0 / 0 (상수 1.0) | 1.0 / 0.0239 | 임베딩 재생성 |
| `similar_count` | 1.0 / 0 (상수 10) | 0.919 / 77.4 | 임베딩 재생성 + 임계값 정의 |
| `per_percentile` | 1.0 / 0 (상수 50) | 1.0 / 23.97 | 코드 수정 (company_features) |
| `pbr_percentile` | 1.0 / 0 (상수 50) | 1.0 / 26.10 | 코드 수정 (company_features) |
| `bayes_momentum_1d` | 0 / 0 | 1.0 / 0.0163 | 코드 수정 (해석적 폴백) |
| `bayes_momentum_5d` | 0 / 0 | 1.0 / 0.0188 | 〃 |
| `bayes_volatility` | 0 / 0 | 1.0 / 0.357 | 〃 |
| `bayes_gain_uncertainty` | 0 / 0 | 1.0 / 0.0121 | 〃 |
| `momentum_3_12m` | 0 / 0 | 0.541 / 0.326 | KRX 백필 66영업일 |
| `relative_strength` | 0 / 0 | 0 / 0 | **못 살림** — `feature_pipeline.py` 하드코딩(수정 금지) → 패치 인계 |

내 담당 14개 중 **13개 부활**, 1개(`relative_strength`)는 수정 금지 파일 때문에 인계.
상수(std=0)였던 내 담당 5개(`avg_similarity_top10`, `max_similarity`, `similar_count`,
`per_percentile`, `pbr_percentile`)는 **전부 해소**되었다(남은 상수는 fx/oil 6개 — 타 담당).

## 1. 피처별 원인 (실측 근거)

### 1.1 `atr_pct` — 지역 dict 에 없는 키로 나눗셈
`services/xgboost-ml/app/feature_engine/market_features.py::get_technical_features`:

```python
        if "macd" in features and "atr" in features:
            atr = features.get("atr", 0.0)
            features["atr_pct"] = float(features["price"] and atr / features["price"] * 100) if features.get("price", 0) else 0.0
```

`get_technical_features` 는 **자기 지역 `features` dict** 만 채우는데 `price` 키는
`get_all_features()` 에서 별도 dict(`get_price_features`) 를 `update()` 로 병합될 때만
존재한다. 따라서 `features.get("price", 0)` 는 **항상 0** → 마지막 `else 0.0` 만 실행.
(단, `atr` 자체는 살아있다 — 같은 블록의 `features["atr"]` 는 `TechnicalIndicatorCalculator`
가 만든 `atr` 컬럼에서 정상 계산된다. 실측 `feature_coverage`: `atr` std 668, `atr_pct` 0.)

### 1.2 `bb_position` — 존재하지 않는 컬럼(`bb_middle`)을 요구
같은 파일:

```python
        if "bb_middle" in df.columns and "close" in df.columns:
            bb_mid = df["bb_middle"].values[-1]
            ...
            features["bb_position"] = float((close_val - bb_mid) / bb_mid * 100) if bb_mid else 0.0
```

`app/processors/technical_indicators.py::calculate_all` 는 `sma_20 / rsi / macd / atr`
**4개만** 생성한다(파일 docstring 에도 명시). `bb_middle` 컬럼은 어디에서도 만들어지지
않으므로 조건이 항상 거짓 → 0.0.

### 1.3 `relative_strength` — 파이프라인 하드코딩 (수정 금지 파일)
`feature_pipeline.py::_build_advanced_features` 435~441행에서 `market_return = 0.0`
하드코딩 → `stock_ret / market_return` 이 항상 0.0. 동시에 `sector_momentum`(436행 위)과
`market_breadth`(445행)도 `stock_prices` 테이블(존재하지 않음)을 조회해 0.0 이다.
지수 테이블도 리포지토리에 없다 → 시세 기반 동일가중 시장수익률 프록시 패치를 인계
(`reports/feature_revival/HANDOFF_relative_strength.md`, 프록시 실측값 포함).

### 1.4 유사도 4개 — 임베딩이 퇴화(3개뿐)
```
stock_vectors 총 행 2773 / 서로 다른 임베딩 3개
md5 별 그룹: 1831건, 939건, 3건
005930 기준 코사인 유사도 분포: 1.0 (938건, 같은 그룹) / 0.66696 (1834건)
```
원인은 `services/stock-vectorizer/app/main.py`:
```python
                price_vector = self.vectorizer.vectorize_price_pattern(stock_code)   # 내부에서 vectorize(None) → 영벡터
                sentiment_vector = self.vectorizer.vectorize_sentiment(stock_code)   # 내부에서 vectorize([]) → 영벡터
```
`CombinedVectorizer.vectorize_price_pattern()` 은 `PriceVectorizer.vectorize(close_prices=None)`,
`vectorize_sentiment()` 은 `SentimentVectorizer.vectorize([])` 를 호출한다 → combined 1024d 의
**앞 512차원이 항상 0**. 남는 것은 fundamental 블록과 통계 4차원뿐이라 전 종목이 3가지로 뭉갰다.
같은 그룹이면 코사인 유사도가 정확히 1.0 → `avg_similarity_top10=1.0`, `max_similarity=1.0`,
`similarity_std=0` 상수. `similar_count` 는 `len(top_k)` 라서 항상 10.

### 1.5 베이즈 4개 — `fit()` 을 아무도 부르지 않는다
런타임 로그(재현): `BayesFactorFeatures.compute called before fit(); no cached posterior
available — returning default bayes features (0.0)`.
코드상 `fit()` 호출자는 `app/training/fit_effective_score.py`(오프라인, `bayes_factors.pkl` 생성)
하나뿐이고, 학습/스코어링 피처 경로(`feature_pipeline.build_features` 125행)는
`self.bayes_factors.compute(close_arr)` 만 부른다 — 즉 **후보 캐시를 로드하지 않는다**.
그래서 4개 모두 0.0 기본값.

### 1.6 `per_percentile` / `pbr_percentile` — 두 개의 독립 버그
`company_features.py::get_percentile_features`(종전):
```python
        features = {"per_percentile": 50.0, "pbr_percentile": 50.0}   # 새 지역 dict
        ...
        my_per = features.get("per_current", 0)   # 'per_current' 키가 없다 → 0
        if per_vals and my_per > 0:               # 항상 거짓
```
게다가 초입에 `sector` 이 없으면 즉시 50.0 을 반환했다. 실측:
```
instrument_type | total | sector_filled
STOCK           |  2796 |      0        ← 일반 주식은 섹터가 하나도 없다
ETF             |  1175 |   1175
ETN             |   369 |    369
industry(전체)  : 0건
```
→ 섹터 JOIN 결과가 항상 비고, 있더라도 `per_current` 키 부재로 계산 자체가 불가능.

### 1.7 `momentum_3_12m` — 252 거래일 조건 vs 248일 시세
`factor_features._momentum_factors`: `ret_12m = close[-1]/close[-252]-1 if valid >= 252 else ret_3m`
→ `valid` 가 252 미만이면 `ret_12m == ret_3m` 이므로 `momentum_3_12m = ret_12m - ret_3m = 0`.
실측: `market_data` = 2025-09-17 ~ 2026-09-23, **248 거래일**(종목당 248행 2523종목) → 전 종목 0.

## 2. 실제로 고친 것 (코드)

| 파일 | 변경 |
|---|---|
| `services/xgboost-ml/app/feature_engine/market_features.py` | `atr_pct` 는 df 의 종가로, `bb_position` 은 OHLCV 에서 직접 Bollinger %B(20,2σ) 계산 |
| `services/xgboost-ml/app/feature_engine/company_features.py` | 자기 per/pbr 를 DB 에서 조회, 섹터 없으면 시장 전체 피어 폴백, `date` 옵션 지원(미래참조 금지) |
| `services/xgboost-ml/app/feature_engine/bayes_factor_features.py` | `fit()` 없을 때 MCMC 대신 선형-가우시안 상태공간 모형의 **해석적(칼만) 사후** 폴백 |
| `services/xgboost-ml/app/feature_engine/vector_features.py` | 후보 풀 300개 조회, `similar_count` = 코사인 유사도 ≥ 0.99 이웃 수, `date` 옵션 추가 |
| `services/stock-vectorizer/app/main.py` | 실제 시세(거래정지 행 제외)/감성 데이터를 벡터라이저에 전달 |
| `services/stock-vectorizer/app/vectorizers/sentiment_vectorizer.py` | Decimal → float 강제(임베딩 저장 실패 14/20 → 0건) |
| `services/stock-vectorizer/app/vectorizers/fundamental_vectorizer.py` | 섹터 해시를 `hash()` → `md5` 로(실행마다 임베딩이 달라지던 비결정성 제거) |
| `services/xgboost-ml/tests/test_bayes_factors.py` | `compute_without_fit_returns_defaults` → `..._uses_analytic_fallback` (죽은 동작을 고정하던 테스트 갱신) |

`feature_pipeline.py` 는 지시대로 **손대지 않았다**.

### 2.1 유사도 임베딩 재생성 (실측)
```
재생성 전 : stock_vectors 2773행 / 서로 다른 임베딩 3개
재생성 후 : stock_vectors 4314행 / 서로 다른 임베딩 3040개   (약 6분, docker exec)
005930 상위 12 이웃 유사도: 0.99533 0.99467 0.99175 0.97640 0.97144 0.97052
                          0.95413 0.94942 0.94583 0.93562 0.91525 0.89732
```
실행: `docker exec stock_vectorizer python /app/scripts/rebuild_vectors.py`
(신규 파일, 야간 스케줄과 동일 로직. `--limit N` 으로 점검 실행 가능)
`similar_count` 임계값은 실측으로 정했다: 상위 50위 이웃의 유사도가 0.96~0.999 라
0.90/0.95 는 전 종목 포화(=상수)였다 → 0.99 에서 종목별로 0~474 로 갈린다.

## 3. KRX 시세 백필 (`momentum_3_12m`)

- 명령: `python3 scripts/krx_daily.py --from 20250616 --to 20250916`
  (`PROJ_DIR=/home/jhshi/analyist_dd POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434`, `.env` 사용)
- 비용 실측: **67영업일 × 2콜 = 134콜, 26초/영업일 → 총 33분** (3.0s + 지터, 기존 정책 그대로)
- 실행 결과: `완료: 66영업일 적재, 휴장 1일, 180338행 upsert, 호출 134회`
  → `market_data` 2025-06-16 ~ 2026-09-23, **314 거래일**(248 → 314)
- 효과(실측): `momentum_3_12m` 9/9 nonzero, std 0.0966, 9 distinct, 3종목 전부 날짜에 따라 변함
- 주의(실측으로 확인된 한계): `build_training_features` 는 시세를 `start_date - 365일` 창으로만
  로드하므로 365일 ≈ 247~255 거래일이 상한이다. 백필 데이터가 패널에 들어가려면
  패널 시작일이 lookback 경계보다 앞서야 한다(30일 패널 → lookback 2025-08-24 →
  해당 구간 265 거래일 ≥ 252 OK). 단일 종목 경로(`build_features` 의 `LIMIT 250`)는
  여전히 252 미만이라 그 경로에서는 `momentum_3_12m` 이 0 이다.
  파이프라인의 lookback/LIMIT 을 키우면 전 구간에서 살릴 수 있다(수정 금지 파일 → 인계 항목).

## 4. 검증 (실측)

### 4.1 피처 × (종목,날짜) — `scripts/verify_feature_revival.py`
3종목 × 3날짜(2026-09-23 / 09-09 / 08-26), 컨테이너 `stock_xgboost_ml`, **KRX 백필 완료 후**:

```
stock_code  date        hist  atr      atr_pct  bb_position  avg_sim   max_sim   sim_std     sim_cnt
000070      2026-09-23  297   1373.45  2.35987   0.0757079   0.998331  0.998769  0.0002384   189
000070      2026-09-09  287   1497.89  2.48405   0.512101    0.998331  0.998769  0.0002384   189
000070      2026-08-26  277   1749.97  2.93619   0.591877    0.998331  0.998769  0.0002384   189
000145      2026-09-23  314   132.379  1.44993   0.0759747   0.9888    0.992204  0.00165307    2
000145      2026-09-09  304   199.248  2.16573   0.298459    0.9888    0.992204  0.00165307    2
000145      2026-08-26  294   267.365  2.92521  -0.146248    0.9888    0.992204  0.00165307    2
000225      2026-09-23  314   118.145  3.2547    0.15221     0.990035  0.993313  0.00141735    3
000225      2026-09-09  304   148.855  4.05598   0.455911    0.990035  0.993313  0.00141735    3
000225      2026-08-26  294   140.88   3.881     0.140217    0.990035  0.993313  0.00141735    3

stock_code  date        per_pct   pbr_pct  bayes_mom1d  bayes_mom5d  bayes_vol  bayes_unc  mom_3_12m  rel_strength
000070      2026-09-23  10.5488   2.86585  -0.00150261  -0.00149451  0.57334    0.00209928  -0.382556  0
000070      2026-09-09  10.5488   2.86585  -0.00143121  -0.00142382  0.580979   0.00216412  -0.3592    0
000070      2026-08-26  10.5488   2.86585  -0.00152537  -0.0015243   0.590364   0.00223856  -0.39635   0
000145      2026-09-23  50        50       -0.00311121  -0.00140491  0.234782   0.00918814  -0.174663  0
000145      2026-09-09  50        50       -0.00118629   0.00095791  0.238422   0.00933742  -0.230756  0
000145      2026-08-26  50        50       -0.0139649   -0.00576417  0.241564   0.00936562  -0.173291  0
000225      2026-09-23  50        50       -0.00403322  -0.00247081  0.282378   0.0104402   -0.207094  0
000225      2026-09-09  50        50       -0.000913111 -0.00222062  0.286036   0.0106002   -0.232289  0
000225      2026-08-26  50        50       -0.00278041  -0.00298687  0.288284   0.0106524   -0.112331  0
```

```
                  feature nonzero  nonzero_ratio        std  n_distinct       min         max alive
                      atr     9/9         1.000    654.682           9    118.145     1749.97   OK
                  atr_pct     9/9         1.000    0.781357          9    1.44993      4.05598  OK
                 bb_width     0/9         0.000    0              0      -           -       DEAD ← 담당 아님
               bb_position    9/9         1.000    0.228190          9   -0.146248     0.591877 OK
        relative_strength     0/9         0.000    0              1    0            0       DEAD ← 파이프라인 하드코딩
     avg_similarity_top10     9/9         1.000    0.00423175        3    0.9888       0.998331 OK
           max_similarity     9/9         1.000    0.00286913        3    0.992204     0.998769 OK
           similarity_std     9/9         1.000    0.00061885        3    0.0002384    0.00165307 OK
            similar_count     9/9         1.000   87.9179           3    2            189      OK
similar_stocks_return_avg     9/9         1.000    0.0136721         3   -0.0330037  -0.000373684 OK
           per_percentile     9/9         1.000   18.5975           2   10.5488       50       OK
           pbr_percentile     9/9         1.000   22.2192           2    2.86585      50       OK
        bayes_momentum_1d     9/9         1.000    0.00386663        9   -0.0139649  -0.000913111 OK
        bayes_momentum_5d     9/9         1.000    0.00167724        9   -0.00576417  0.00095791 OK
         bayes_volatility     9/9         1.000    0.151985          9    0.234782     0.590364 OK
   bayes_gain_uncertainty     9/9         1.000    0.00369686        9    0.00209928   0.0106524 OK
           momentum_3_12m     9/9         1.000    0.0965594         9   -0.39635     -0.112331 OK
      momentum_1m_reverse     8/9         0.889    0.0259958         9   -0.0548673    0.039666 OK
```

날짜 민감도(종목 3개 각각, 3날짜 간 값이 달라지는 피처): **9/18**
→ `atr, atr_pct, bb_position, bayes 4개, momentum_3_12m, momentum_1m_reverse`.

날짜에 따라 변하지 **않는** 담당 피처와 그 이유:
- 유사도 4개: `stock_vectors` 는 종목당 1개(최신 60일) 벡터라 시간축이 없다 → 설계상 종목 간 변동만 존재
  (이번에 `date` 인자를 지원하도록 만들었지만 파이프라인은 date 를 넘기지 않는다).
- `per/pbr_percentile`: 재무제표 기반이라 분기 단위로만 변한다(위 표본에서는 2개 값).
- `similar_stocks_return_avg`: date 미전달 → 최신 5일 수익률(파이프라인 수정 필요).

### 4.2 `feature_coverage` 갱신 — 동일 표본 before/after (확정)

- **before**: `reports/feature_revival/coverage_before_20260924.csv`
  (2026-09-23 18:51 UTC = 03:51 KST 스냅샷, `--stocks 30 --days 30`, 수정 전 코드)
- **after**: `docker exec stock_xgboost_ml python scripts/feature_coverage_report.py --ignore-cache --stocks 30 --days 30`
  → `source=축소 빌드(30종목/30일) window_days=30 features=167`
  → `죽은 피처(=nonzero_ratio 0) 53개 / 살아있는 114개`

| | before | after |
|---|---|---|
| nonzero_ratio == 0 (완전 죽음) | 68 | 53 |
| nonzero 이지만 std == 0 (상수) | 11 | 6 |
| **죽음+상수 합계** | **79** | **59** |

부활한 피처 23개 중 **13개가 이 자식 담당**:

```
atr_pct              (0.0, 0.0)      -> (1.0, 2.095)
avg_similarity_top10 (1.0, 0.0)      -> (1.0, 0.02468)     ← 상수 해소
bayes_gain_uncertainty(0.0, 0.0)     -> (1.0, 0.01205)
bayes_momentum_1d    (0.0, 0.0)      -> (1.0, 0.01635)
bayes_momentum_5d    (0.0, 0.0)      -> (1.0, 0.01883)
bayes_volatility     (0.0, 0.0)      -> (1.0, 0.35697)
bb_position          (0.0, 0.0)      -> (1.0, 0.30430)
max_similarity       (1.0, 0.0)      -> (1.0, 0.02394)     ← 상수 해소
momentum_3_12m       (0.0, 0.0)      -> (0.541, 0.32604)
pbr_percentile       (1.0, 0.0)      -> (1.0, 26.097)      ← 상수 해소
per_percentile       (1.0, 0.0)      -> (1.0, 23.968)      ← 상수 해소
similar_count        (1.0, 0.0)      -> (0.919, 77.431)    ← 상수(10) 해소
similarity_std       (0.0, 0.0)      -> (1.0, 0.002526)
```

같은 실행에서 다른 담당 계열 10개도 부활했다(`economic_event_*`, `event_contract_5d`,
`event_new_product_5d`, `event_regulation_5d`, `krx_total_trading_value`, `quality_beta`,
`quality_cp_to_assets`, `value_pcr`, `value_pfcr`) — 이 자식의 변경이 아니므로 참고만.

⚠️ 관찰: `event_delisting_5d`, `event_partnership_5d`, `event_realized_5d` 3개가
이번 패널에서 0으로 나왔다(이전엔 살아있었음). 이벤트 피처 경로는 이 자식이 건드리지
않았다 — 해당 담당자가 확인 필요.

> 표본 일치: before/after 모두 `--stocks 30 --days 30`(seed 0, 결정적 유니버스) 이므로 직접 비교 가능.
> 단, before 스냅샷(03:51)과 after(05:0x) 사이에 **다른 자식들의 코드 변경**도 반영되어 있어,
> 위 표의 부활 23개에는 타 담당 기여분이 섞여 있다(내 몫은 13개).

## 5. 못 살린 피처와 정확한 이유

| 피처 | 상태 | 이유 |
|---|---|---|
| `relative_strength` | DEAD | `feature_pipeline.py` 436~441행 하드코딩(`market_return=0.0`). 수정 금지 파일 → 패치 인계 |
| `bb_width` | 패널에 컬럼 없음 | `get_feature_names()` (모델 계약 173개) 에는 있는데 `build_features` 가 아무 데서도 만들지 않는다(내 3종목 검증에서도 NaN). 내 담당 목록에 없어 손대지 않았다 — `bb_position` 과 같은 블록에서 `(upper-lower)/mid` 3줄로 계산 가능 |
| `sector_momentum`, `market_breadth` | DEAD | 존재하지 않는 `stock_prices` 테이블 조회 (파이프라인) |
| `stocks.sector` | 미채움 | 아래 §6 |
| `momentum_1m_reverse` 8/9 | 정상 | 특정 (종목,날짜)에서 실제 수익률이 0이었던 경우(결측 아님) |

`momentum_3_12m` 은 이제 살아있지만 **파이프라인 lookback 상한**(365일 ≈ 247~255 거래일)에
걸려 있다 — 패널 시작일이 경계보다 앞서야 한다. 전 구간 보장은 1줄 패치 2개(인계 문서 §6).

## 6. `stocks.sector` 채우기 판단 (실행하지 않음)

- 실측: 일반 주식 2796종목 중 섹터 보유 **0건**(ETF/ETN 1544건만 채워짐), `industry` 0건.
- 승인된 무료 소스: 리포지토리 정책상 KRX OpenAPI 는 일별매매정보 2개 서비스만 사용한다
  (`services/krx-collector`, `scripts/krx_daily.py`). 업종분류는 IP 차단 이력이 있는
  스크래핑 엔드포인트(`comm/bldAttendant/getJsonData.cmd`)로만 접근 가능 → **정책상 호출 금지**.
- DART `company.json`(induty_code)은 종목당 1콜 × 2796 이 필요하고, 지금 돌고 있는
  `dart_financial_backfill.py` 와 레이트리밋을 다퉈야 해서 이번 범위에서 제외.
- 대신 `get_percentile_features` 가 섹터 부재 시 **시장 전체 피어**로 폴백하도록 고쳤고,
  나중에 섹터가 채워지면 같은 코드가 자동으로 섹터 상대 백분위로 전환된다.

## 7. ⚠️ 인계 필수 — stock_vectorizer 컨테이너 재시작

`stock_vectorizer` 프로세스는 **약 8시간 전 코드를 메모리에 들고** 돌고 있다
(`run_scheduled`: 시작 시 1회 + 매일 20:00 실행). 코드는 바인드 마운트되어 있어
`docker exec` 로 새로 띄우는 프로세스는 수정된 `main.py` 를 쓰지만(이번 재생성이 그렇게 했다),
**20:00 야간 실행은 옛 코드(=영벡터 임베딩)로 돌아가 재생성 결과를 덮어쓴다.**

- 조치: 담당자가 `docker restart stock_vectorizer` (또는 배포) 1회.
- 이 자식은 지시상 컨테이너 재시작이 금지되어 실행하지 않았다.

## 8. 되돌리기
```
# 코드 (전부 git 추적 파일)
git checkout -- services/xgboost-ml/app/feature_engine/market_features.py \
                services/xgboost-ml/app/feature_engine/company_features.py \
                services/xgboost-ml/app/feature_engine/bayes_factor_features.py \
                services/xgboost-ml/app/feature_engine/vector_features.py \
                services/stock-vectorizer/app/main.py \
                services/stock-vectorizer/app/vectorizers/sentiment_vectorizer.py \
                services/stock-vectorizer/app/vectorizers/fundamental_vectorizer.py \
                services/xgboost-ml/tests/test_bayes_factors.py
# (vector_features.py 는 이번에 재작성했으므로 'git checkout' 으로 원복된다)

# DB — 시세 백필 되돌리기(백필분만 삭제)
DELETE FROM market_data WHERE trade_date < '2025-09-17';

# DB — 임베딩은 재생성으로 자동 복구되지 않는다(원복하려면 옛 코드로 재생성해야 함)
#      참고: 재생성 전 상태는 '서로 다른 임베딩 3개'(재현 가능한 퇴화 상태)였다.

# feature_coverage 는 스크립트 재실행으로 언제든 덮어쓴다
docker exec stock_xgboost_ml python scripts/feature_coverage_report.py --ignore-cache
```
