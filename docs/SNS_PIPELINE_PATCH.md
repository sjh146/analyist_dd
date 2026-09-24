# SNS 피처 → 학습 파이프라인 연결 패치 (인계용)

작성: 2026-09-24 새벽 작업. 대상 파일: `services/xgboost-ml/app/feature_engine/feature_pipeline.py`
(**이 작업에서는 수정하지 않았다** — 동시 편집 충돌 회피. 아래를 적용해야 한다.)

## 0. 전제 (적용 순서)

1. `sns_feature_bundle.py` 가 리포에 있어야 한다 (이미 추가됨, 새 파일).
2. `sns_post_features` 를 최신 `sns_posts` 로 **refresh** 해 둔다
   (`python scripts/sns_features_writer.py --lookback-days 0`).
   백필로 과거 게시글이 늘면 이 테이블도 다시 써야 과거 구간 피처가 생긴다.
3. 패치 적용 후 **재학습 필요**: 피처 이름 173 → 199 로 늘어나므로 기존 챔피언
   모델의 feature 순서/개수와 호환되지 않는다. (`.pkl` 재사용 금지)

## 1. 패치 (unified diff, `feature_pipeline.py` 현재 리비전 기준)

```diff
--- a/services/xgboost-ml/app/feature_engine/feature_pipeline.py
+++ b/services/xgboost-ml/app/feature_engine/feature_pipeline.py
@@ -22,6 +22,7 @@
 from app.feature_engine.kalman_filter import KalmanFeatureFilter
 from app.feature_engine.bayes_factor_features import BayesFactorFeatures
 from app.feature_engine.news_event_features import NewsEventFeatures
+from app.feature_engine.sns_feature_bundle import SnsFeatureBundle, feature_names
 
 logger = logging.getLogger(__name__)
 
@@ -133,6 +134,18 @@
         # News event features (market impact, event taxonomy, theme exposure)
         features.update(self.news_events.get_all_features(stock_code, self.pg_conn))
 
+        # SNS(네이버 종목토론방) 피처 — sns_posts × sns_post_features × market_data.
+        # 번들은 종목별 프리페치 캐시를 가지므로 (종목,날짜) 반복 호출에서
+        # DB 조회가 종목당 1회로 줄어든다. date 를 넘겨 룩어헤드를 차단한다.
+        # 데이터가 없으면 빈 dict → 기존과 동일하게 0.0 결측 처리(fail-open).
+        try:
+            bundle = getattr(self, "_sns_bundle", None)
+            if bundle is None or bundle.pg_conn is not self.pg_conn:
+                bundle = self._sns_bundle = SnsFeatureBundle(pg_conn=self.pg_conn)
+            features.update(bundle.load(stock_code, date=date))
+        except Exception as e:
+            logger.debug(f"SNS features failed for {stock_code}: {e}")
+
         # Real sentiment from stock_sentiment table
         sentiment = self._get_stock_sentiment(stock_code, date)
         features.update(sentiment)
@@ -960,6 +973,9 @@
             # Quality score (F-Score, 0~1)
             "quality_score",
 
+            # SNS features (sns_posts × sns_post_features × market_data)
+            *feature_names(),
+
             # News event features (market impact, event taxonomy, theme exposure)
             "market_impact_score",
             "event_realized_5d", "event_mna_5d", "event_capital_increase_5d",
```

수동 적용 시 앵커(라인 번호는 패치 전 파일 기준):

- L24 `from app.feature_engine.news_event_features import NewsEventFeatures` 바로 아래에 import 1줄.
- L133-134 `# News event features ...` + `features.update(self.news_events.get_all_features(...))` 블록 **뒤에** try 블록.
  (`__init__` 은 건드리지 않는다 — `self.pg_conn` 은 L44 에서 대입되므로 L43 에서
  `SnsFeatureBundle(pg_conn=self.pg_conn)` 을 쓰면 `AttributeError` 다.)
- L963 `# News event features (market impact, ...)` 앞에 `*feature_names(),` 1줄.

`build_training_features()` 는 내부에서 `build_features(code, date_str, market_df=...)` 를
호출하므로 **추가 패치 없이 학습 패널에도 자동으로 들어간다**.

## 2. `get_feature_names()` 에 추가되는 이름 26개

`feature_names()` (`sns_feature_bundle.py`) 가 단일 소스다. 명시적으로 적으면:

```
sns_sentiment_score            sns_attention_score            sns_momentum_score
sns_author_quality_score       sns_post_count                 sns_bot_filtered_count
kalman_sentiment               kalman_attention               kalman_momentum
kalman_activity
sns_sentiment_score_best_lag   sns_sentiment_score_max_corr
sns_sentiment_score_lag_sign   sns_sentiment_score_corr0
sns_attention_score_best_lag   sns_attention_score_max_corr
sns_attention_score_lag_sign   sns_attention_score_corr0
sns_momentum_score_best_lag    sns_momentum_score_max_corr
sns_momentum_score_lag_sign    sns_momentum_score_corr0
sns_author_quality_score_best_lag  sns_author_quality_score_max_corr
sns_author_quality_score_lag_sign  sns_author_quality_score_corr0
```

이름은 리더가 실제로 반환하는 키에서 왔다(실측 확인):
`compute_for_stock` → `sentiment_score, attention_score, momentum_score,
author_quality_score, post_count, bot_filtered_count, kalman_sentiment,
kalman_attention, kalman_momentum, kalman_activity` (앞 6개만 `sns_` 접두),
`get_all_features` → `sns_{feat}_{best_lag|max_corr|lag_sign|corr0}` 16개(그대로).

**이름 충돌 없음(실측)**: 기존 `get_feature_names()` 173개 ∩ SNS 26개 = **0개**.

## 3. 값 계약 / 선택 옵션

`SnsFeatureBundle(pg_conn, use_lag=True, window_days=None, source="table")`

| source | 동작 | 값 출처 | 실측 비용 |
|---|---|---|---|
| `table` (기본) | `sns_post_features` 의 (종목,일) 행을 그대로 읽는다 | writer(=리더 date=None) 계산값 | 26키 32.5ms/호출 (캐시 워밍 후) |
| `window` | 매 호출 리더로 재계산(`<= date`, 30일 트레일링) | 리더 date= 경로 | 26키 ~113ms/호출 |

- `window` 는 `SnsFeatures.compute_for_stock(code, conn, date=d)` /
  `SnsLagFeatures.get_all_features(code, conn, date=d)` 와 **완전 일치**(실측 불일치 0).
- `table` 은 `sns_post_features` 행과 **완전 일치**(실측 불일치 0). 대신 테이블이
  date=None(전체 이력) 계산이라 창 경계(칼만/모멘텀) 값이 `window` 와 미세하게 다를 수 있다.
  학습/추론 일관성은 `table` 쪽이 더 안전(둘 다 같은 테이블을 읽으므로).
- 26개 전부를 매 (종목,날짜)마다 만들면 250종목 × 150일 = 37,500회 × 33ms ≈ **20분**이
  build 단계에 추가된다. 다음 단계 권장: writer 를 확장해 16개 시차 피처도
  (종목,일) 테이블로 materialize 하면 학습 시 비용이 0 이 된다.

## 4. 알려진 한계 (반드시 같이 읽을 것)

1. **시차 16개는 SNS 커버 일수가 부족하면 0** 이다. `MIN_PAIRS=8` + lag ±5 라
   공통 일자가 ~13일 이상 있어야 값이 산다. 실측: `sns_post_features` 일수가 2일인
   종목(034020/035420/005380 @2026-09-23)은 시차 16개가 전부 0,
   16일인 종목(303810)은 12개가 비영. → 커버리지가 곧 피처 품질이다(백필 필요).
2. **과거 구간은 enrich 미적용**(백필 러너 기본 `--enrich-recent-days 15`).
   목록 응답은 `recommendCount/commentCount` 가 0 이므로 과거일의
   `author_quality_score` 가 0 에 가깝고 `attention_score` 는 게시글 수 기반으로
   퇴화한다. 전 구간 보강은 요청이 3배(같은 시간에 깊이 1/3).
   - **실측 수치**: `author_quality_score` 비영 비율이 최근(≥2026-09-10, 보강됨)
     **0.978** vs 과거(<09-10, 미보강) **0.155** (테이블 기준).
   - **주의(부동소수 잔차)**: 미보강 일자의 리더 출력은 정확히 0 이 아니라
     `4.23e-09` 다(`post_author_quality` 의 `_EPS` 때문). DB 컬럼이
     `numeric(6,4)` 라 테이블에는 0.0000 으로 저장된다. "비영 비율"을
     `!= 0.0` 으로 재면 raw 0.922 / tol(1e-6) 0.371 로 갈린다
     → 커버리지 지표는 **허용오차 1e-6** 또는 DB 컬럼 기준으로 재야 한다.
   - 따라서 이 피처는 과거 구간에서 **'최근인가'를 알려주는 대리변수**가 될 수
     있다. 모델에 넣기 전에 (a) 전 구간 보강 백필을 돌리거나 (b) `author_quality`
     계열(4개: 자체 + 시차 3개... 실제로는 시차 4개 전부)을 제외하는 판단이 필요하다.
3. `kalman_activity` 는 종전에 **윈도우 마지막 날 = 항상 0** 이던 off-by-one 이
   있었다(`smoothed` 길이가 n-1). 날짜 기준 조회는 조회일이 항상 마지막이라
   피처가 통째로 상수가 됐다 → 리더에서 수정했다(아래 변경 이력).
   `sns_post_features` 는 **수정 전 코드로 쓰인 행이 남아 있으므로 refresh 필요**.
4. `bot_filtered_count` 는 원래도 희소(전체 3,455행 중 35행 비영)하다 — 정상.

## 5. 검증 명령 (재현용)

```bash
# 컨테이너 안 (cwd=/app)
python -u scripts/sns_feature_probe.py --pairs 3 --reader-stocks 250   # 커버리지/날짜 조회 실측
python -u scripts/sns_pipeline_patch_check.py --pairs 3                 # 번들 == 리더 값 일치
python -u scripts/sns_pipeline_integration_check.py --pairs 3           # 서브클래스로 패치 효과 재현
python -u scripts/sns_patch_apply_check.py                              # 패치 사본 실제 적용 검증
```
