#!/usr/bin/env bash
# 챔피언 다중창 견고 AUC 실측을 **세션과 분리**해 띄운다.
# 왜 setsid 인가: Hermes 세션 정리(SIGKILL)가 자식 프로세스를 함께 죽인 실측 전례가 있다
# (스킬: "런처는 세션과 함께 죽는다 — setsid 로 띄워라"). 70분짜리 측정이라 분리가 필요하다.
set -u
cd /home/jhshi/analyist_dd || exit 2
LOG=data/reports/me_cycle/champion_robust_eval.log
setsid nohup docker exec stock_xgboost_ml sh -c \
  'cd /app && OMP_NUM_THREADS=4 timeout 4200 python -u scripts/champion_robust_eval.py \
   --folds 5 --dates-per-fold 10 --stocks 80 --out /app/reports/champion_robust_eval.json' \
  > "$LOG" 2>&1 < /dev/null &
echo "launched pid=$! log=$LOG"
