#!/usr/bin/env bash
# CG278 — 돈 지표 측정기 수리: 덤프가 '미래 극단(상·하위 30%)'만 담던 문제를 고쳐
#          전 유니버스(라벨 결측 포함) 위에서 배포가능 arm(CO_smooth_d1_h5)의 초과기대를 잰다.
#
# WHY(2026-11-14 CG277 실측): wf_label_sweep 덤프는 `d = d[~isna(_y)]` 뒤에 만들어져
#   세션당 118행(=200종목의 59% · y_true 정확히 50/50)만 남는다 → 풀평균이 '미래 상하위 30%의
#   평균'이지 거래가능 풀평균이 아니다. 그 덤프로 낸 Δ(예: CG277 h5 k3 +1.759 · t 3.51)는
#   실전 초과기대로 읽을 수 없다. `--dump-all-codes`(기본 OFF, 비트 동일)로 전 유니버스를
#   같은 모델로 채점해 y_true=None 행으로 싣고, 같은 돈 집계기를 다시 돌린다.
#
# 프로토콜(사전등록): 같은 패널·같은 폴드·같은 시드 · exit close_h · 수수료 왕복 0.21%p ·
#   체결성 필터 ON · 널 = 세션 풀평균(전 유니버스).
#   성공: k∈{3,5,10} 중 하나라도 Δ(top-k − 풀평균) ≥ +0.1%p/세션 & t ≥ 2 & 앞/뒤 절반 부호 동일.
#   미달이면 '배포가능 arm 의 돈 축 종결'(AUC·돈 모두 근거를 갖춘 종결).
set -u
cd /home/jhshi/analyist_dd || exit 2
HOST_JSON="/home/jhshi/analyist_dd/services/xgboost-ml/reports/overnight/cg278_money_allcodes.json"
PREDS="/app/reports/overnight/cg278_preds_allcodes.jsonl"
MONEY="/app/reports/overnight/cg278_money_allcodes.json"

echo "[$(date '+%F %T')] CG278 시작 — 전 유니버스 덤프(2 arm × 5폴드 × 3시드)"
docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=4 timeout 2400 python -u scripts/wf_label_sweep.py \
  --panel /app/app/models/wf/panel_prod200.npz --folds 5 --seeds 3 \
  --only CO_smooth_d1_h5,CG276_h1 --dump-preds $PREDS --dump-all-codes \
  --out /app/reports/overnight/cg278_sweep.jsonl --summary-out /app/reports/overnight/cg278_sweep_summary.json" 2>&1 | tail -12
rc=${PIPESTATUS[0]}
echo "[$(date '+%F %T')] 스윕 rc=$rc"
[ $rc -eq 0 ] || exit $rc

echo "[$(date '+%F %T')] 돈 집계 — 배포가능 arm(CO_smooth_d1_h5) top-k vs 전 유니버스 풀평균"
docker exec stock_xgboost_ml sh -c "cd /app && python -u scripts/fillable_topk_horizon_sweep.py \
  --arm-jsonl $PREDS --arm-tag CO_smooth_d1_h5 \
  --control-jsonl $PREDS --control-tag CG276_h1 \
  --k 3,5,10,20,30 --horizons 1,3,5,10 --primary 5 \
  --json-out $MONEY"
mrc=$?
echo "[$(date '+%F %T')] 돈 집계 rc=$mrc → $HOST_JSON"
exit $mrc
