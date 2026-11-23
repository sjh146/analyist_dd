# curated 피처 + 얕은 트리 레시피 (0.60+ 재현)

## 왜 이 조합인가

- 라벨이 '다음날 종가 상승'(up_rate ≈0.47)이면 신호 대 잡음비가 낮아 피처를 173개 다 넣어도 val/test ≈0.51 에 머문다.
- 저장소 이력에서 0.60+ 를 낸 것은 **curated 소수 피처 + 얕은 트리(lr 0.03 · depth 4 · est 1500)** 조합이다.
  같은 설정을 두 번 돌려 0.6031 / 0.6060 이 나왔고(재현됨), 같은 계열 변형은 0.592~0.595, 과도하게 큰 depth/lr 조합은 0.55~0.57 이었다.
- curated + sentiment/news 포함(43피처 · 50종목) 조합은 0.6205.
- 원본 템플릿은 `scripts/ml_infinite_loop.sh` 안의 `train_vN.py` heredoc — 버전별 자동 루프가 실제로 돌리던 스크립트다.

## 레시피 (순서대로)

1) 유니버스(결정적 선택 — 재현성의 핵심):
   ```sql
   SELECT md.stock_code FROM market_data md JOIN stocks s ON md.stock_code = s.stock_code
   WHERE s.market = 'KOSDAQ' AND md.trade_date >= '<약 6개월 전>' GROUP BY md.stock_code
   HAVING COUNT(*) >= 50 ORDER BY md.stock_code LIMIT 50
   ```
2) `Trainer.prepare_training_data(stock_codes=..., days=180)` → 7-tuple `(X_train, X_val, X_test, y_train, y_val, y_test, feature_names)`.
   6개로 unpack 하면 `ValueError: too many values to unpack`.
3) train 상승라벨 오버샘플링으로 균형화.
4) curated 이름 목록(48개) 중 `feature_names` 와 교집합만 사용 — 실측 43개:
   `ma_position_5/20/60/120`, `net_income`, `net_margin`, `op_margin`, `operating_profit`, `price`, `return_5d`, `return_20d`,
   `revenue`, `similar_stocks_return_avg/std`, `volatility_20d/60d`, `volume_ratio_5/20`, `momentum_vs_volatility`,
   `trend_interaction`, `volume_price_trend`, `cross_trend`, `volatility_volume`, `short_medium_term_momentum`,
   `trend_confirmation`, `price_volume`, `return_5d_mean_10d`, `volatility_20d_mean_10d`, `volume_ratio_5_mean_10d`,
   `rank_return_5d/20d`, `rank_volatility_20d`, `rank_volume_ratio_5/20`, `rank_ma_position_5`, `target_ma_5/10/20`,
   `momentum_1m_reverse`, `quality_score`, `kalman_momentum_1d/5d`, `kalman_volatility`,
   `sentiment_avg/5d/20d`, `news_count_5d/20d`
5) train 을 67/33 으로 다시 쪼개 앞부분=학습, 뒷부분=validation 으로 사용.
6) 하이퍼파라미터 override: `learning_rate=0.03`, depth `4`, `n_estimators=1500`.
   주의: xgboost 는 `max_depth`, catboost 는 `depth` 키를 쓴다 — 동시에 설정하면 충돌한다.
7) 평가: test(약 1,200샘플) 에서 `roc_auc_score`. 시드 0~4 평균±std 로 보고.

## 함정

- 구 프로덕션은 "AUC 가 이전보다 높으면 `cp *.pkl app/models/champion/`" 로 승격했다 — 그 방식을 따라 하지 않는다.
  지금은 `champion_promote.py` 게이트로만 승격한다.
- 소표본(50종목 · test 1,200)이라 분산이 크다 → 단일 시드 값으로 성공/실패를 선언하지 않는다.
- `catboost_info/` 와 `reports/*.json` 은 학습 때마다 갱신되는 부산물이라 git status 노이즈가 된다(커밋 대상 아님으로 취급).
- `bayes_*` 피처는 사후분포 fit 을 하지 않으면 항상 0 이고 그 경고가 로그를 뒤덮어 진행 확인을 방해한다
  → 진행 확인은 `grep -v bayes_factor` 로 필터해 읽는다.
