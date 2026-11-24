#!/bin/bash
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1
docker exec -w /app -e PYTHONPATH=/app stock_xgboost_ml \
  python /app/scripts/signal_registry.py >> reports/cron_signal_registry.log 2>&1
RC=$?
if [ "$RC" -eq 0 ]; then
  docker cp stock_xgboost_ml:/tmp/signal_registry.json data/reports/signal_registry.json
  docker cp stock_xgboost_ml:/tmp/signal_registry.md data/reports/signal_registry.md
fi
echo "[signal_registry] rc=$RC $(date '+%F %T')" >> reports/cron_signal_registry.log
exit "$RC"
