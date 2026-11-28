"""유동성 통과 종목만 대상으로 ATR 분위 순기대를 다시 잰다.

왜 필요한가: 전체 종목 기준 ATR 분위(Q1 +0.068% → Q5 +0.611%)는 **비유동 종목이 섞인**
분포다. 실제 매매 유니버스는 거래대금 50억·가격 1000원 이상이라 분포가 다르다.
필터를 켜기 전에 '유동성 안에서도 ATR 하한이 값을 하는가'를 확인한다.

사용:
    python3 scripts/measure_atr_quintile_liquid.py --min-tv 5000000000
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict

import numpy as np
import psycopg2


def load_env(path: str = "/home/jhshi/analyist_dd/.env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def pg():
    load_env()
    return psycopg2.connect(host="127.0.0.1", port=5434,
                            dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
                            user=os.environ.get("POSTGRES_USER", "stock_user"),
                            password=os.environ.get("POSTGRES_PASSWORD", ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-tv", type=float, default=5_000_000_000)
    ap.add_argument("--min-price", type=float, default=1000)
    ap.add_argument("--stop", type=float, default=0.0075)
    ap.add_argument("--target", type=float, default=0.05)
    ap.add_argument("--cost", type=float, default=0.23)
    args = ap.parse_args()

    conn = pg()
    cur = conn.cursor()
    cur.execute("""
        select stock_code, trade_date::text, open_price::float8, high_price::float8,
               low_price::float8, close_price::float8, trading_value::float8
        from market_data where open_price > 0 order by stock_code, trade_date
    """)
    rows = cur.fetchall()
    cur.execute("""select stock_code, trade_date::text, time, close_price::float8
                   from minute_bars_unified where close_price > 0
                   order by stock_code, trade_date, time""")
    bars = defaultdict(list)
    for c, d, t, p in cur.fetchall():
        bars[(str(c), d)].append((int(t), p))
    conn.close()

    trs, info = defaultdict(list), {}
    prev = {}
    for c, d, o, h, l, cl, tv in rows:
        pc = prev.get(c, cl)
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs[c].append(tr)
        atr = float(np.mean(trs[c][-14:])) if len(trs[c]) >= 7 else None
        prev[c] = cl
        if atr and cl > 0:
            info[(c, d)] = (atr / cl * 100.0, tv, cl, o)

    data = []
    for key, (atr_pct, tv, cl, o) in info.items():
        if tv < args.min_tv or cl < args.min_price:
            continue
        series = bars.get(key)
        if not series:
            continue
        up, dn = o * (1 + args.target), o * (1 - args.stop)
        payoff = None
        for _, px in sorted(series):
            if px >= up:
                payoff = args.target * 100
                break
            if px <= dn:
                payoff = -args.stop * 100
                break
        if payoff is None:
            payoff = (sorted(series)[-1][1] / o - 1.0) * 100
        data.append((atr_pct, payoff - args.cost))

    if not data:
        print("표본 없음")
        return 1
    arr = np.array(data)
    atr = arr[:, 0]
    net = arr[:, 1]
    qs = np.quantile(atr, [0.2, 0.4, 0.6, 0.8])
    print(f"유동성 통과 표본 {len(arr):,} 종목·일 (거래대금 {args.min_tv/1e8:.0f}억+, 비용 {args.cost}%)")
    print(f"{'분위':>4} {'ATR%구간':>16} {'표본':>7} {'순기대':>9} {'t':>6}")
    edges = [0] + list(qs) + [float("inf")]
    for i in range(5):
        sel = (atr >= edges[i]) & (atr < edges[i + 1])
        v = net[sel]
        if len(v) < 30:
            continue
        t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
        hi = f"{edges[i+1]:.2f}" if np.isfinite(edges[i + 1]) else "∞"
        print(f"{i+1:>4} {edges[i]:6.2f}~{hi:>7} {len(v):>7,} {v.mean():+8.3f}% {t:+6.2f}")
    print(f"\n전체 평균 {net.mean():+.3f}% (표준편차 {net.std(ddof=1):.3f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
