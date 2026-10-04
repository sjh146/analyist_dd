#!/bin/sh
# _cg100_rerun.sh — CG100 재기동(스레드 2) + 종료 사유 기록.
#
# 배경(2026-10-04 실측): 1차 실행이 폴드 1(AUC 0.4772)까지만 찍고 **트레이스백 없이 사라졌다**
# (산출물 0, 로그 멈춤, 프로세스 소멸). 호스트 메모리 7GB/여유 4GB 에서 300종목×8일 피처빌드를
# 4스레드로 돌린 것이 유력 원인 → 스레드를 2로 낮추고, **종료코드·메모리 피크·시각**을 남겨
# 다음에 죽으면 원인을 추정이 아니라 기록으로 말한다.
set -u
cd /app || exit 1
LOG=/app/scripts/_cg100_run.log
echo "[cg100r] start $(date '+%F %T') uid=$(id -u)" >> "$LOG"
OMP_NUM_THREADS=2 timeout 7200 python scripts/champion_robust_eval.py \
  --model-dir app/models/wf/cg92_q05 --label-kind quantile --label-q 0.05 --horizon 5 \
  --folds 10 --dates-per-fold 8 --stocks 300 --universe training --universe-seed 7 \
  --train-start 2025-08-22 --train-end 2025-11-20 \
  --dump-preds /app/reports/overnight/cg100_q05_all.jsonl --dump-tag cg92_q05 --dump-all \
  --out /app/reports/overnight/cg100_q05_robust.json >> "$LOG" 2>&1
RC=$?
PEAK=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes 2>/dev/null || echo '?')
echo "[cg100r] step1 rc=$RC peak_mem=$PEAK $(date '+%F %T')" >> "$LOG"
if [ "$RC" -eq 0 ]; then
  OMP_NUM_THREADS=2 timeout 7200 python scripts/champion_robust_eval.py \
    --model-dir app/models/wf/cg92_q30 --label-kind quantile --label-q 0.30 --horizon 5 \
    --folds 10 --dates-per-fold 8 --stocks 300 --universe training --universe-seed 7 \
    --train-start 2025-08-22 --train-end 2025-11-20 \
    --dump-preds /app/reports/overnight/cg100_q30_all.jsonl --dump-tag cg92_q30 --dump-all \
    --out /app/reports/overnight/cg100_q30_robust.json >> "$LOG" 2>&1
  echo "[cg100r] step2 rc=$? $(date '+%F %T')" >> "$LOG"
  python scripts/fillable_topk_expectancy.py \
    --arm-jsonl /app/reports/overnight/cg100_q05_all.jsonl --arm-tag cg92_q05 \
    --control-jsonl /app/reports/overnight/cg100_q30_all.jsonl --control-tag cg92_q30 \
    --k 3,5,10,20,30 --primary-side bottom --exit close_h --horizon 5 \
    --json-out /app/reports/overnight/cg100_money_bottom_seed7.json >> "$LOG" 2>&1
  echo "[cg100r] step3 rc=$? $(date '+%F %T')" >> "$LOG"
fi
echo "[cg100r] done $(date '+%F %T')" >> "$LOG"
