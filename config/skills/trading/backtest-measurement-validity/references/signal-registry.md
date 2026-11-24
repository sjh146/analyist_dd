# 신호 레지스트리 — 피처별 "비용을 넘는 돈" 판정 레시피

목적: 피처를 살아있나(분산·커버리지)로만 판정하면 변동성·상수 여부만 걸러지고 **돈이 되는지**는 모른다.
이 레지스트리는 235개 피처를 IC(순위)와 **절대 순수익(비용 차감)** 두 축으로 동시에 판정한다.

## 실행

    cd /home/jhshi/analyist_dd
    docker exec -w /app -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/signal_registry.py
    docker cp stock_xgboost_ml:/tmp/signal_registry.json data/reports/signal_registry.json
    docker cp stock_xgboost_ml:/tmp/signal_registry.md   data/reports/signal_registry.md

- 컨테이너는 스크립트를 `/tmp` 에 쓴다(레포 마운트가 read-only 인 경로가 있음) → **docker cp 를 빼먹으면 호스트 산출물이 낡은 채로 남는다.**
  크론은 `cron/signal_registry.sh`(주 1회, 토 06:00)가 실행+cp 를 같이 한다.
- 입력 패널: `services/xgboost-ml/app/models/wf/panel_prod200_idn.npz` (`X`, `feature_names`, `dates`, `codes`, `price`).
  실행 시간 약 100~160초/235피처. 진행 로그(`[signal_registry] 25/235 …`)로 생존 확인.

## 계산 규칙 (스크립트 상수로 고정 · 테스트로 잠금)

| 항목 | 값 | 이유 |
|---|---|---|
| IC | 날짜별 횡단면 Spearman, 유효쌍 `MIN_PAIRS` 이상만 | 순위 기반이라 액면분할·이상치에 강함 |
| t | `mean(IC)/std(IC)·√n_days` | 표본일수 대비 유의성 |
| 돈 지표 | h5 상위 10% 의 **절대** 전방수익 평균 − 왕복비용 | 중위수 차감 상대수익은 우측꼬리 때문에 거의 전부 +로 보인다 |
| 초과수익 | 상위 10% − 날짜 중위수 − 비용 | IC 와 나란히 볼 비교용(채택 판정에는 쓰지 않음) |
| `MAX_ABS_FWD` | 0.5 | \|fwd\| > 50% 는 액면분할·단절(수정주가 미적용). 실측 348/61,640행 |
| `MIN_DAYS` | 60 | 최근에만 값이 있는 피처는 `n_days=1` 에서 IC 0.25 로 1위를 차지한다 |
| 판정 | `alive` \|IC_h5\|≥0.010 & \|t\|≥2.0 · `weak` 경계 · `dead` 그 외 · `insufficient` 표본<60일 | 규칙은 `classify(ic, t, n_days)` 순수 함수 |

## 산출물 계약

```json
{"generated_at": "...", "rows": 61640,
 "features": [{"name": "...", "ic_h1": 0.01, "ic_h5": 0.02, "ic_h20": null,
               "t_h5": 5.87, "coverage": 1.0, "top10_net_h5": 0.0048,
               "top10_excess_h5": 0.0144, "status": "alive", "n_days": 309}],
 "summary": {"alive": 86, "weak": 47, "dead": 22, "insufficient": 80}}
```

MD 는 \|IC\| 상위 25(표본충분만) + 판정별 개수 + **절대 순수익이 플러스인 피처 목록**.

## 읽는 법 (실측 예)

- 표본충분 155개 중 **절대 순수익 플러스 11개**. 대부분의 "살아있는" 피처는 비용을 못 넘는다.
- \|IC\| 상위는 **변동성 계열**(ATR IC −0.098 · t −8.2 · 순수익 −1.3%) → 고변동성이 5일 상대 부진.
  생산 스코어가 변동성에 가중을 주고 있으면 그 가중과 정면 충돌하므로 그리드 실험(0/중간/최대)으로 결판낸다.
- t 가 크고 순수익도 (+)인 후보는 `quality_score`(IC 0.052 · t 9.5 · +0.26%/5일),
  `similar_stocks_return_avg`(IC 0.049 · t 5.9 · +0.48%/5일) 정도 — **한계선**이므로 승격 전 OOS 재확인.
- `t=None` 인데 \|IC\| 가 큰 피처는 거의 전부 `insufficient`(n_days 부족)다. 이걸 상위에 두면 결론이 뒤집힌다.

## 검증 절차

1. `python3 -m pytest tests/test_signal_registry.py -q` — 심은 신호 alive / 난수 dead / 경계 분류 / 표본부족 / 스키마.
2. 재실행 멱등성: 같은 패널이면 `summary` 카운트가 동일한지.
3. 산출물 신선도: `docker cp` 후 `ls -l data/reports/signal_registry.json` 의 mtime 과 `generated_at` 일치.
