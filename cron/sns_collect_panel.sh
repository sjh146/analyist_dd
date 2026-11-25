#!/bin/bash
set -u
LOG=/home/jhshi/analyist_dd/reports/cron_sns_panel.log
CODES=$(/usr/bin/python3 - <<'PY'
import numpy as np
z = np.load('/home/jhshi/analyist_dd/data/panels/panel_prod200_v6.npz', allow_pickle=True)
print(','.join(sorted({str(c) for c in z['codes']})))
PY
)
echo "[$(date '+%F %T')] panel SNS 수집 시작 (${#CODES} bytes)" >> "$LOG"
timeout 3000 docker exec -w /app stock_news_analyzer \
    python -u scripts/sns_collect_runner.py --codes "$CODES" --max-pages "${SNS_MAX_PAGES:-2}" --flush-every 25 \
    >> "$LOG" 2>&1
rc=$?
echo "[$(date '+%F %T')] panel SNS 수집 종료 rc=$rc" >> "$LOG"
exit "$rc"
