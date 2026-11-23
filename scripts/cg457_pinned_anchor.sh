#!/usr/bin/env bash
# CG457 — 앵커 격자를 **유니버스 고정**으로 재측정한다.
# 근거(2026-11-24 실측): 같은 플래그·같은 앵커의 CG455(00:2x)와 CG456(01:1x) 가 서로 다른 모집단을
# 뽑았다(aafd016ed4bc vs 89a7b5d7f0bc) — 같은 앵커 2026-11-07 의 챔피언 AUC 가 0.4790 vs 0.5052
# (Δ0.0262 = 사전문턱 +0.02 의 1.3배). 즉 CG455 의 앵커 std 0.0382 에는 창 길이 효과 + 모집단
# 재추첨 효과가 섞여 있다. 첫 앵커에서 코드 목록을 떠서(--universe-codes-out) 나머지 앵커가 그 목록을
# 그대로 쓰게 하면(--universe-codes-in) 앵커·창 길이 효과만 남는다.
set -uo pipefail
REPO=${PROJ_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
OUTDIR=/app/reports/overnight
HOSTOUT=$REPO/services/xgboost-ml/reports/overnight
LOG=$REPO/data/reports/me_cycle/logs/cg457_pinned_anchor.log
mkdir -p "$HOSTOUT" "$(dirname "$LOG")"
CODES=$OUTDIR/cg457_universe_codes.json
CFG="--universe training --label-kind rel --horizon 5 --folds 1 --dates-per-fold 24 --stocks 60 --oos-start 2026-10-02 --train-end 2026-10-01"
ANCHORS=${ANCHORS:-"2026-10-24 2026-10-31 2026-11-07 2026-11-14 2026-11-21"}
rm -f "$HOSTOUT/cg457_universe_codes.json"
echo "=== start $(date '+%F %T') anchors: $ANCHORS (유니버스 고정 모드)" >>"$LOG"
first=1
for a in $ANCHORS; do
  if [ "$first" -eq 1 ]; then
    EXTRA="--universe-codes-out $CODES"
    first=0
  else
    EXTRA="--universe-codes-in $CODES"
  fi
  echo "=== [$(date '+%F %T')] anchor=$a $EXTRA" >>"$LOG"
  docker exec stock_xgboost_ml sh -c \
    "cd /app && OMP_NUM_THREADS=2 timeout 1200 python -u scripts/champion_robust_eval.py $CFG $EXTRA --asof-date $a --model-dir app/models/champion --out $OUTDIR/cg457_pt_$a.json" >>"$LOG" 2>&1
  echo "    rc=$?" >>"$LOG"
done
CG455_PREFIX=cg457_pt CG455_OUT=cg457_anchor_level.json \
  python3 "$REPO/scripts/_cg455_anchor_agg.py" "$HOSTOUT" "$REPO/data/reports/overnight" >>"$LOG" 2>&1
echo "DONE" >>"$LOG"
