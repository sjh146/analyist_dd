#!/usr/bin/env bash
# CG470 — 유니버스 폭 사다리: 같은 arm·같은 패널·같은 폴드/시드에서 limit 50/100/200
# 왜: 등록 기준선(0.5406)·최근 판정(0.5475·0.5519)은 전부 limit 50(=49~50종목) 표본에서 측정됐고,
#     생산 패널은 200종목이다. 같은 런 안에서 폭만 바꿔 '폭 효과'를 분리한다(측정 정합성).
# 사용: bash scripts/cg470_breadth_ladder.sh   (호스트에서 실행, 컨테이너 stock_xgboost_ml 사용)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PANEL=/app/app/models/wf/panel_prod200_v6o.npz
CFG=CG376_noroll_h5
STAMP=$(date +%Y%m%d_%H%M)
LOG=data/reports/me_cycle/logs/cg470_breadth_${STAMP}.log
mkdir -p data/reports/me_cycle/logs

echo "[cg470] start $(date '+%F %T') panel=$PANEL cfg=$CFG" | tee -a "$LOG"

for L in 50 100 200; do
  echo "[cg470] limit=$L start $(date '+%T')" | tee -a "$LOG"
  docker exec -e L="$L" stock_xgboost_ml sh -c '
    cd /app && OMP_NUM_THREADS=4 timeout 5400 python -u scripts/wf_label_sweep.py \
      --panel '"$PANEL"' \
      --days 420 --limit "$L" --folds 5 --seeds 5 \
      --only '"$CFG"' \
      --out /app/reports/overnight/cg470_l${L}.jsonl \
      --summary-out /app/reports/overnight/cg470_l${L}_summary.json \
      --dump-preds /app/reports/overnight/cg470_l${L}_preds.jsonl
  ' >> "$LOG" 2>&1
  rc=$?
  echo "[cg470] limit=$L rc=$rc $(date '+%T')" | tee -a "$LOG"
done

echo "[cg470] done $(date '+%F %T')" | tee -a "$LOG"
python3 - "$LOG" <<'PY'
import json, statistics, sys, glob
rows = []
for p in sorted(glob.glob("services/xgboost-ml/reports/overnight/cg470_l*_summary.json")):
    d = json.load(open(p))
    per = d.get("per_exp") or {}
    lim = p.split("_l")[-1].split("_")[0]
    for k, v in per.items():
        f = v.get("folds") or []
        rows.append((lim, k, v.get("mean"), v.get("std"),
                     sum(1 for x in f if x > 0.5) / len(f) if f else None, f))
for r in rows:
    print(f"limit={r[0]:>3} arm={r[1]} mean={r[2]} std={r[3]} foldwin={r[4]:.2f} folds={r[5]}")
if len(rows) >= 2:
    base = [r for r in rows if r[0] == "50"]
    for r in rows:
        if base and r[0] != "50" and base[0][5] and r[5] and len(base[0][5]) == len(r[5]):
            ds = [a - b for a, b in zip(r[5], base[0][5])]
            print(f"Δ(limit{r[0]}−limit50) mean={statistics.mean(ds):+.4f} "
                  f"pos={sum(1 for x in ds if x>0)}/{len(ds)} ds={[round(x,4) for x in ds]}")
PY
