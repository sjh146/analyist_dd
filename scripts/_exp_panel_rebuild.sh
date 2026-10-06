#!/bin/bash
# 2026-10-06 엔지니어: R14 차단 해소 — exp_panel 캐시(200종목×120일)를 현재 DB 로 재빌드.
cd /home/jhshi/analyist_dd
LOG=data/reports/me_cycle/logs/exp_panel_rebuild_20261006.log
echo "[rebuild] start $(date -Is)" >> "$LOG"
docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 9000 python -u scripts/ml_auc_experiment.py build --stock-limit 200 --days 120 --cache-dir app/models/exp_panel' >> "$LOG" 2>&1
echo "[rebuild] rc=$? end $(date -Is)" >> "$LOG"
