#!/bin/sh
# E2/E3 정규화 + scale_pos_weight 스윕 (멀티시드 3개). 결과는 reports/retrain/exp_sweep.log
set -e
cd /app
BASE="python -u scripts/ml_auc_experiment.py multiseed --cache-dir app/models/exp_panel --n-estimators 300 --seeds 0,1,2"

run() {
  echo "=== $1 ==="
  $BASE $2 2>&1 | grep -vE "Training until|Early stopping|best iteration|\[[0-9]+\]"
  echo
}

{
echo "E2/E3 SWEEP $(date -Iseconds)"
echo "baseline(재현, 3seed): depth8 lr0.05 col0.7 mc3 reg1.0 spw1.4"
run "A_baseline" "--max-depth 8 --learning-rate 0.05 --min-child 3 --reg-lambda 1.0 --colsample 0.7 --subsample 0.7 --scale-pos-weight 1.4"

run "B_reg" "--max-depth 4 --learning-rate 0.03 --min-child 5 --reg-lambda 5.0 --colsample 0.6 --subsample 0.7 --scale-pos-weight 1.4"

run "C_reg_mild" "--max-depth 5 --learning-rate 0.03 --min-child 5 --reg-lambda 2.0 --colsample 0.6 --subsample 0.7 --scale-pos-weight 1.4"

run "D_spw1.0" "--max-depth 8 --learning-rate 0.05 --min-child 3 --reg-lambda 1.0 --colsample 0.7 --subsample 0.7 --scale-pos-weight 1.0"

run "E_spw2.1" "--max-depth 8 --learning-rate 0.05 --min-child 3 --reg-lambda 1.0 --colsample 0.7 --subsample 0.7 --scale-pos-weight 2.1"

run "F_combo" "--max-depth 5 --learning-rate 0.03 --min-child 5 --reg-lambda 2.0 --colsample 0.6 --subsample 0.7 --scale-pos-weight 2.1"

echo "SWEEP DONE $(date -Iseconds)"
} > reports/retrain/exp_sweep.log 2>&1
