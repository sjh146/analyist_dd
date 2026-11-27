import os
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
cur.execute("select distinct trade_date::text from market_data where trade_date >= '2026-09-01' order by 1")
days = [r[0] for r in cur.fetchall()]
pos = {d: i for i, d in enumerate(days)}

cur.execute("select prediction_date::text, stock_code, confidence from ml_predictions where prediction_date between '2026-10-01' and '2026-11-21'")
pred = {}
for d, code, conf in cur.fetchall():
    pred.setdefault(d, []).append((str(code), float(conf)))

cur.execute("select stock_code, trade_date::text, close_price from market_data where trade_date >= '2026-09-01' and close_price > 0")
px = {(str(c), d): float(p) for c, d, p in cur.fetchall()}
conn.close()

ics, spreads = [], []
for d, items in sorted(pred.items()):
    if d not in pos or pos[d] + 5 >= len(days):
        continue
    d5 = days[pos[d] + 5]
    xs, ys = [], []
    for code, conf in items:
        p0 = px.get((code, d))
        p1 = px.get((code, d5))
        if p0 and p1:
            xs.append(conf)
            ys.append(p1 / p0 - 1.0)
    if len(xs) < 50:
        continue
    xs = np.array(xs)
    ys = np.array(ys)
    if xs.std() == 0:
        continue
    rx = np.argsort(np.argsort(xs))
    ry = np.argsort(np.argsort(ys))
    ics.append(float(np.corrcoef(rx, ry)[0, 1]))
    hi = xs >= np.quantile(xs, 0.8)
    lo = xs <= np.quantile(xs, 0.2)
    spreads.append(float(ys[hi].mean() - ys[lo].mean()))

a = np.array(ics)
s = np.array(spreads) * 100
print(f"예측일 {len(a)}개")
print(f"모델확률 ↔ 5일수익 rank IC 평균 {a.mean():+.4f} · t {a.mean()/(a.std()/np.sqrt(len(a))+1e-12):+.2f}")
print(f"상위20%-하위20% 5일 스프레드 평균 {s.mean():+.3f}% · t {s.mean()/(s.std()/np.sqrt(len(s))+1e-12):+.2f}")
print("IC 분위(10/50/90)", np.round(np.percentile(a, [10, 50, 90]), 3))
