"""손익비 셀의 승률·체결 분포 — 기대값만 보면 '낮은 승률'을 놓친다."""
import os
from collections import defaultdict

import numpy as np
import psycopg2

env = {}
for line in open('/home/jhshi/analyist_dd/.env', encoding='utf-8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
conn = psycopg2.connect(host='127.0.0.1', port=5434, dbname=env.get('POSTGRES_DB', 'stock_trading'),
                        user=env.get('POSTGRES_USER', 'stock_user'),
                        password=env.get('POSTGRES_PASSWORD', ''))
cur = conn.cursor()
cur.execute("select stock_code, trade_date::text, open_price from market_data where open_price > 0")
opens = {(str(c), d): float(o) for c, d, o in cur.fetchall()}
cur.execute("select stock_code, trade_date::text, time, close_price from minute_bars_unified where close_price > 0 order by 1,2,3")
bars = defaultdict(list)
for c, d, t, p in cur.fetchall():
    bars[(str(c), d)].append((int(t), float(p)))
conn.close()

for stop, target in ((0.005, 0.05), (0.0075, 0.05), (0.01, 0.03), (0.01, 0.05)):
    hit = miss = close = 0
    for key, series in bars.items():
        o = opens.get(key)
        if not o:
            continue
        up, dn = o * (1 + target), o * (1 - stop)
        res = None
        for _, px in sorted(series):
            if px >= up:
                res = 'target'
                break
            if px <= dn:
                res = 'stop'
                break
        if res == 'target':
            hit += 1
        elif res == 'stop':
            miss += 1
        else:
            close += 1
    tot = hit + miss + close
    print(f"손절 {stop*100:.2f}% / 목표 {target*100:.2f}% → 목표도달 {hit/tot:5.1%} · 손절 {miss/tot:5.1%} · 종가청산 {close/tot:5.1%}  (n={tot:,})")
