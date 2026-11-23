#!/usr/bin/env bash
# CG456 — post-train 창에서 '챔피언 vs 재학습 후보(retrain_cg346)' 를 **같은 앵커 5개**로 짝 비교한다.
# 근거(CG455 실측 2026-11-24): 같은 모델·같은 학습구간에서 창 상한만 옮기면 AUC 가 0.4790~0.5889
# (범위 0.1099 = 사전문턱 +0.02 의 5.5배)로 흔들린다 → 단일 앵커·단일 창의 절대 비교는 무의미하고,
# **같은 앵커의 짝 Δ 평균**만 앵커 잡음을 상쇄한다. 그래서 두 모델을 앵커별로 짝지어 잰다.
set -uo pipefail
REPO=${PROJ_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
OUTDIR=/app/reports/overnight
HOSTOUT=$REPO/services/xgboost-ml/reports/overnight
LOG=$REPO/data/reports/me_cycle/logs/cg456_posttrain_paired.log
mkdir -p "$HOSTOUT" "$(dirname "$LOG")"
CFG="--universe training --label-kind rel --horizon 5 --folds 1 --dates-per-fold 24 --stocks 60 --oos-start 2026-10-02 --train-end 2026-10-01"
ANCHORS=${ANCHORS:-"2026-10-24 2026-10-31 2026-11-07 2026-11-14 2026-11-21"}
ARMS=${ARMS:-"champion retrain_cg346"}
echo "=== start $(date '+%F %T') anchors: $ANCHORS arms: $ARMS" >>"$LOG"
for a in $ANCHORS; do
  for m in $ARMS; do
    echo "=== [$(date '+%F %T')] anchor=$a arm=$m" >>"$LOG"
    docker exec stock_xgboost_ml sh -c \
      "cd /app && OMP_NUM_THREADS=2 timeout 1200 python -u scripts/champion_robust_eval.py $CFG --asof-date $a --model-dir app/models/$m --out $OUTDIR/cg456_${m}_$a.json" >>"$LOG" 2>&1
    echo "    rc=$?" >>"$LOG"
  done
done
python3 "$REPO/scripts/_cg456_paired_agg.py" "$HOSTOUT" >>"$LOG" 2>&1
echo "DONE" >>"$LOG"
