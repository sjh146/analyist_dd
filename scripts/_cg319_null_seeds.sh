#!/usr/bin/env bash
# CG319 — 널 분포(순열 실현 여러 개). 한 순열 실현만으로는 '편향'인지 '그 실현의 운'인지 모른다.
# 폴드가 서로 겹치므로 fold std 는 독립 표본이 아니다 → 실현(seed) 간 산포가 진짜 널 분포다.
set -u
for i in 2 3 4 5 6; do
  seed=$((20261116+i))
  docker exec stock_xgboost_ml python /app/scripts/_cg319_null_panel.py \
    --dst "/app/app/models/wf/panel_null_s${seed}.npz" \
    --out "/app/reports/overnight/cg319_null_panel_s${seed}.json" \
    --seed "$seed" >/dev/null 2>&1 || { echo "PANEL_FAIL seed=$seed"; continue; }
  docker exec stock_xgboost_ml sh -c "cd /app && OMP_NUM_THREADS=3 timeout 900 python -u scripts/wf_label_sweep.py \
    --panel /app/app/models/wf/panel_null_s${seed}.npz --folds 5 --seeds 3 --only LS_quant_q30_h5 \
    --out /app/reports/overnight/cg319_null_s${seed}.jsonl \
    --summary-out /app/reports/overnight/cg319_null_s${seed}.json" >/dev/null 2>&1
  echo "seed=$seed rc=$?"
done
echo DONE
