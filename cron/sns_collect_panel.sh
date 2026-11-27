#!/bin/bash
# 종토방 SNS 수집 — 패널 200종목 ∪ 거래대금 상위 250종목 (2026-11-27 통합).
#
# 이전에는 17:30(top250)·17:35(패널200) 두 잡이 같은 종목을 각각 크롤해 요청이 중복됐다.
# 유니온으로 합치면 같은 커버리지를 한 번의 실행으로 얻고, 크롤 시간도 줄어든다.
# 우선순위: 패널 종목을 먼저 넣어 창이 잘려도 연구용 종목이 먼저 채워진다.
set -u
LOG=/home/jhshi/analyist_dd/reports/cron_sns_panel.log
CODES=$(/usr/bin/python3 - <<'PY'
import json
import numpy as np

z = np.load('/home/jhshi/analyist_dd/data/panels/panel_prod200_v8.npz', allow_pickle=True)
panel = [str(c) for c in sorted({str(x) for x in z['codes']})]
try:
    import psycopg2, os
    env = {}
    for line in open('/home/jhshi/analyist_dd/.env', encoding='utf-8'):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    conn = psycopg2.connect(host='127.0.0.1', port=5434,
                            dbname=env.get('POSTGRES_DB', 'stock_trading'),
                            user=env.get('POSTGRES_USER', 'stock_user'),
                            password=env.get('POSTGRES_PASSWORD', ''))
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code FROM market_data
        WHERE trade_date = (SELECT max(trade_date) FROM market_data)
        GROUP BY stock_code ORDER BY SUM(trading_value) DESC NULLS LAST LIMIT 250
    """)
    top = [str(r[0]) for r in cur.fetchall()]
    conn.close()
except Exception:
    top = []
seen, out = set(), []
for code in panel + top:
    if code not in seen:
        seen.add(code)
        out.append(code)
print(','.join(out))
PY
)
echo "[$(date '+%F %T')] SNS 수집 시작 (종목 ${#CODES} bytes)" >> "$LOG"
timeout 3000 docker exec -w /app stock_news_analyzer \
    python -u scripts/sns_collect_runner.py --codes "$CODES" --max-pages "${SNS_MAX_PAGES:-2}" --flush-every 25 \
    >> "$LOG" 2>&1
rc=$?
echo "[$(date '+%F %T')] SNS 수집 종료 rc=$rc" >> "$LOG"
exit "$rc"
