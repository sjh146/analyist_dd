#!/bin/sh
# _cg100_run.sh — CG100 사전등록 재현 실행기 (컨테이너 안에서 실행).
#
# WHY: CG98/CG99 는 seed 0 유니버스에서 'top-k 가 풀평균보다 낮고 하위 k 가 높다'는 힌트를 봤지만
# 사전문턱 t≥2 에 못 미쳤다. 같은 데이터에서 본 힌트이므로 **서로소 유니버스(seed 7)** 에서 같은
# 방향이 재현되는지가 승격 근거의 선행 조건이다(docs/QUANT_MODEL_BACKLOG.json CG100).
#
# 로그: /app/scripts/_cg100_run.log (= 호스트 scripts/_cg100_run.log, 쓰기 가능 경로)
set -u
cd /app || exit 1
echo "[cg100] start $(date '+%F %T')"
echo "[cg100] step1 arm q0.05 (seed 7)"
OMP_NUM_THREADS=4 timeout 7200 python scripts/champion_robust_eval.py \
  --model-dir app/models/wf/cg92_q05 --label-kind quantile --label-q 0.05 --horizon 5 \
  --folds 10 --dates-per-fold 8 --stocks 300 --universe training --universe-seed 7 \
  --train-start 2025-08-22 --train-end 2025-11-20 \
  --dump-preds /app/reports/overnight/cg100_q05_all.jsonl --dump-tag cg92_q05 --dump-all \
  --out /app/reports/overnight/cg100_q05_robust.json || echo "[cg100] step1 FAILED rc=$?"
echo "[cg100] step2 control q0.30 (seed 7)"
OMP_NUM_THREADS=4 timeout 7200 python scripts/champion_robust_eval.py \
  --model-dir app/models/wf/cg92_q30 --label-kind quantile --label-q 0.30 --horizon 5 \
  --folds 10 --dates-per-fold 8 --stocks 300 --universe training --universe-seed 7 \
  --train-start 2025-08-22 --train-end 2025-11-20 \
  --dump-preds /app/reports/overnight/cg100_q30_all.jsonl --dump-tag cg92_q30 --dump-all \
  --out /app/reports/overnight/cg100_q30_robust.json || echo "[cg100] step2 FAILED rc=$?"
echo "[cg100] step3 money test (bottom k, pool baseline)"
python scripts/fillable_topk_expectancy.py \
  --arm-jsonl /app/reports/overnight/cg100_q05_all.jsonl --arm-tag cg92_q05 \
  --control-jsonl /app/reports/overnight/cg100_q30_all.jsonl --control-tag cg92_q30 \
  --k 3,5,10,20,30 --primary-side bottom --exit close_h --horizon 5 \
  --json-out /app/reports/overnight/cg100_money_bottom_seed7.json || echo "[cg100] step3 FAILED rc=$?"
echo "[cg100] done $(date '+%F %T')"
