# 신호 레지스트리

- 패널: `/app/app/models/wf/panel_prod200_idn.npz` — rows=61640 features=235
- 생성: 2026-11-24T16:01:48+00:00
- 판정 임계: alive |IC_h5|>=0.010 & |t_h5|>=2.0 · weak |IC_h5|>=0.005 | |t_h5|>=1.5 · dead 그 외
- 비용: 왕복 0.21% (상위 10% 예측 종목 평균 수익에서 차감) · IC: 날짜별 횡단면 Spearman, 유효쌍>=10 · t=mean/std*sqrt(n_days)
- 판정: alive 86 · weak 47 · dead 22 · insufficient(표본<60일) 80 (총 235)
- 데이터 이상치: |전방수익|>50% 행은 IC·순수익 계산에서 제외(액면분할·단절 오염 방지)

## |IC_h5| 상위 25

| # | feature | IC_h5 | t_h5 | coverage | top10_net_h5 | top10_excess_h5 | status |
|---|---|---|---|---|---|---|---|
| 1 | sns_post_count | -0.1264 | -3.97 | 0.025 | -2.376% | -2.067% | alive |
| 2 | atr_pct | -0.0984 | -8.16 | 1.000 | -1.300% | -0.339% | alive |
| 3 | bayes_volatility | -0.0956 | -8.23 | 1.000 | -1.401% | -0.440% | alive |
| 4 | volatility_60d | -0.0882 | -7.36 | 1.000 | -1.049% | -0.088% | alive |
| 5 | quality_price_volatility_60d | -0.0882 | -7.36 | 1.000 | -1.049% | -0.088% | alive |
| 6 | kalman_volatility | -0.0862 | -7.27 | 1.000 | -1.256% | -0.296% | alive |
| 7 | volatility_20d | -0.0860 | -7.33 | 1.000 | -1.228% | -0.267% | alive |
| 8 | vix_proxy | -0.0860 | -7.33 | 1.000 | -1.228% | -0.267% | alive |
| 9 | rank_volatility_20d | -0.0860 | -7.33 | 1.000 | -1.228% | -0.267% | alive |
| 10 | beta_60d | -0.0853 | -7.13 | 1.000 | -1.044% | -0.083% | alive |
| 11 | volatility_20d_mean_10d | -0.0818 | -6.93 | 1.000 | -1.054% | -0.093% | alive |
| 12 | volatility_20d_mean_10d | -0.0818 | -6.93 | 1.000 | -1.054% | -0.093% | alive |
| 13 | bb_width | -0.0765 | -7.00 | 1.000 | -1.081% | -0.120% | alive |
| 14 | volatility_volume | -0.0604 | -6.40 | 1.000 | -0.989% | -0.028% | alive |
| 15 | volatility_volume | -0.0604 | -6.40 | 1.000 | -0.989% | -0.028% | alive |
| 16 | net_income | 0.0550 | 8.23 | 1.000 | -0.287% | +0.674% | alive |
| 17 | funding_level_20d | -0.0539 | -8.28 | 0.725 | -1.430% | -0.453% | alive |
| 18 | sns_momentum_score_corr0 | 0.0519 | 2.14 | 0.034 | -0.283% | +0.634% | alive |
| 19 | quality_score | 0.0515 | 9.51 | 1.000 | +0.265% | +1.226% | alive |
| 20 | quality_roa | 0.0506 | 8.39 | 1.000 | -0.445% | +0.516% | alive |
| 21 | volatility_skew | -0.0497 | -8.76 | 1.000 | -1.239% | -0.278% | alive |
| 22 | similar_stocks_return_avg | 0.0493 | 5.87 | 1.000 | +0.478% | +1.439% | alive |
| 23 | net_margin | 0.0484 | 8.42 | 1.000 | -0.424% | +0.537% | alive |
| 24 | quality_roe | 0.0470 | 7.77 | 1.000 | -0.376% | +0.585% | alive |
| 25 | sns_sentiment_score_best_lag | -0.0468 | -1.72 | 0.034 | -1.225% | -0.308% | weak |

## 상위 10% 순수익이 플러스인 피처 (11)

- `kalman_activity` (+4.509%)
- `kalman_sentiment` (+2.867%)
- `sns_sentiment_score` (+2.867%)
- `kalman_momentum` (+1.275%)
- `sns_sentiment_score_lag_sign` (+0.978%)
- `sns_momentum_score_best_lag` (+0.783%)
- `similar_stocks_return_avg` (+0.478%)
- `sns_attention_score_max_corr` (+0.274%)
- `quality_score` (+0.265%)
- `similar_stocks_return_std` (+0.096%)
- `sns_momentum_score_lag_sign` (+0.064%)
