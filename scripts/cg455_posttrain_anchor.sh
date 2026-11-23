#!/usr/bin/env bash
# CG455 — post-train 창(2026-10-02~)에서 앵커 6개 레벨 AUC 를 재어 앵커 잡음을 측정한다.
# CG454 실측: 현 챔피언은 학습 2026-07-03~10-01 이라 기본 창격자에서는 학습 이전 창만 채점된다.
# 우회 경로(folds=1 + --oos-start 2026-10-02)로 최근 시장 창을 고정하고 앵커만 옮긴다.
set -uo pipefail
REPO=${PROJ_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
OUTDIR=/app/reports/overnight
HOSTOUT=$REPO/services/xgboost-ml/reports/overnight
LOG=$REPO/data/reports/me_cycle/logs/cg455_posttrain_anchor.log
mkdir -p "$HOSTOUT" "$(dirname "$LOG")"
CFG="--universe training --label-kind rel --horizon 5 --folds 1 --dates-per-fold 24 --stocks 60 --oos-start 2026-10-02 --train-end 2026-10-01"
ANCHORS=${ANCHORS:-"2026-11-21 2026-11-14 2026-11-07 2026-10-31 2026-10-24 2026-10-17"}
echo "=== start $(date '+%F %T') anchors: $ANCHORS" >>"$LOG"
for a in $ANCHORS; do
  echo "=== [$(date '+%F %T')] anchor=$a" >>"$LOG"
  docker exec stock_xgboost_ml sh -c \
    "cd /app && OMP_NUM_THREADS=2 timeout 1200 python -u scripts/champion_robust_eval.py $CFG --asof-date $a --model-dir app/models/champion --out $OUTDIR/cg455_pt_$a.json" >>"$LOG" 2>&1
  echo "    rc=$?" >>"$LOG"
done
python3 "$REPO/scripts/_cg455_anchor_agg.py" "$HOSTOUT" >>"$LOG" 2>&1
echo "DONE" >>"$LOG"
