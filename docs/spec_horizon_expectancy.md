# 스펙 — 지평별 기대값(돈) 하네스

작성 2026-11-20. 선행 실험: `docs/EXP_horizon_verdict.md` (h1 AUC 0.5568 > h5 0.5266).
**AUC 개선이 돈인지** 확인하는 단계. 이 스크립트가 내는 숫자가 승격 논의의 근거가 된다.

## 1. 신규 `scripts/horizon_expectancy.py`

```
python3 scripts/horizon_expectancy.py --panel /app/app/models/wf/p_v5recent.npz \
    --horizons 1,2,3,5 --folds 4 --top 5 --cost-bps 21 --slip-bps 20 \
    --json-out /app/reports/overnight/horizon_expectancy.json
```

### 절차 (워크포워드, look-ahead 금지)

1. 패널 로드(`scripts/wf_wave.py` 의 `__load_panel`/`build_X` 재사용 가능하면 재사용; 불가하면 동일 규칙으로 재구현).
2. 거래일 정렬 후 **4 분할** — wf_wave 와 같은 방식(확장 창: fold k 는 앞 구간 학습, 다음 구간 테스트).
   학습 구간 끝에서 `horizon` 일 만큼 **purge**(라벨 누출 방지).
3. 각 horizon h 에 대해:
   - 라벨: `close[t+h]/close[t]-1` 의 **분위 0.3**(상위 30%) 이진 분류 — wf_wave 의 `build_label` 규칙과 동일.
   - 피처 선택: 학습 구간 단변량 edge 상위 **top60**(wf_wave `select_features` 재사용).
   - 모델: xgboost 1개 + lightgbm 1개 평균(wf_wave 와 같은 하이퍼파라미터).
4. **기대값 시뮬레이션**(테스트 구간, 거래일별):
   - 각 날짜에서 모델 확률 상위 `--top N` 종목을 뽑는다(동률은 코드 순).
   - 진입: 다음 거래일 시가(패널 `price` 를 종가로 쓰므로 **다음날 종가 진입**으로 근사 — 명시할 것).
   - 청산: h 거래일 뒤 종가.
   - 비용: `--cost-bps`(왕복 수수료·세금) + `--slip-bps`(편도 슬리피지 ×2).
   - 건별 net 수익률을 모아 기대값·승률·표준편차·t값·최대낙폭 계산.
5. 산출 JSON: horizon 별 `{n, mean_net_pct, win_rate, std, t, max_dd_pct, by_fold:[...]}` + `picks_sample`.

### 판정 규칙 (사전 고정)

- 채택 후보 조건: **기대값 > +0.10%/건 AND 표본 ≥ 300건 AND 승률 ≥ 손익분기 승률**
- 이 조건을 하나라도 못 넘으면 "AUC 는 올랐지만 돈은 아니다"로 기록하고 승격하지 않는다.

## 2. 테스트 `tests/test_horizon_expectancy.py` (4개)

① 비용이 기대값을 정확히 깎는다(같은 픽, cost 0 vs 21bps 차이 검증)
② 상위 N 선택이 확률 순서를 지킨다 ③ purge 가 라벨 누출을 막는다(경계일 미포함)
④ 결정적 입력에서 결과 재현(같은 시드 2회 → 동일 mean)

## 3. 검증

```
/usr/bin/python3 -m pytest tests/test_horizon_expectancy.py -q
docker exec -w /app -e OMP_NUM_THREADS=4 stock_xgboost_ml \
  python -u scripts/horizon_expectancy.py --panel /app/app/models/wf/p_v5recent.npz \
  --horizons 1,2,3,5 --folds 4 --top 5 --json-out /app/reports/overnight/horizon_expectancy.json
```

## 4. 금지

- 버전관리 금지. 실주문·브리지·트레이더 코드 무접근. 기존 스크립트·테스트 수정 금지.
- DB 쓰기 금지. look-ahead 금지(진입은 신호일 **다음** 거래일).
- 결과를 좋게 보이게 하는 사후 파라미터 조정 금지(그리드 탐색 없음 — 고정값 1회).
