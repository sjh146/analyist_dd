#!/usr/bin/env python3
"""변동성 분위별 손절/목표 도달 순서 — '고변동이 정말 손절 먼저인가'를 잰다.

2026-11-26 실측: 모델 상위 3종목 순기대(-0.72%)가 전 종목 평균(+0.138%)보다 나빴다.
모델이 변동성 피처로 종목을 고르기 때문이라는 가설을 분위별로 검증한다.

사용:
    python3 scripts/measure_touch_by_vol.py --target 3.0 --stop 1.0
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
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def pg_connect():
    load_env()
    host = os.environ.get("POSTGRES_HOST") or "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT", "5434"))
    if not os.path.exists("/.dockerenv"):
        host = "127.0.0.1" if host == "postgres" else host
        port = 5434 if port == 5432 else port
    return psycopg2.connect(host=host, port=port, dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
                            user=os.environ.get("POSTGRES_USER", "stock_user"),
                            password=os.environ.get("POSTGRES_PASSWORD", ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=3.0)
    ap.add_argument("--stop", type=float, default=1.0)
    ap.add_argument("--quintiles", type=int, default=5)
    args = ap.parse_args()

    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code, trade_date::text, open_price, high_price, low_price, close_price
        FROM market_data
        WHERE trade_date >= '2026-11-10' AND open_price > 0
    """)
    daily = {}
    atr = defaultdict(list)
    for c, d, o, h, l, cl in cur.fetchall():
        o, h, l, cl = float(o), float(h), float(l), float(cl)
        daily[(str(c), d)] = (o, cl)
        if cl > 0:
            atr[str(c)].append((d, (h - l) / cl * 100.0))
    cur.execute("""
        SELECT stock_code, trade_date::text, time, close_price
        FROM minute_close_bars WHERE close_price > 0
        ORDER BY stock_code, trade_date, time
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    vol_pct = {}
    for code, series in atr.items():
        series.sort()
        vals = [v for _, v in series[-20:]]
        if vals:
            vol_pct[code] = float(np.mean(vals))

    bars = defaultdict(list)
    for c, d, tm, px in rows:
        bars[(str(c), d)].append((int(tm), float(px)))

    tgt, stp = args.target / 100.0, args.stop / 100.0
    recs = []
    for key, series in bars.items():
        code, day = key
        q = daily.get(key)
        v = vol_pct.get(code)
        if not q or v is None:
            continue
        o, _ = q
        ordered = sorted(series)
        up, dn = o * (1 + tgt), o * (1 - stp)
        first = None
        for tm, px in ordered:
            if px >= up:
                first = "target"
                break
            if px <= dn:
                first = "stop"
                break
        close_ret = ordered[-1][1] / o - 1.0 if ordered else 0.0
        recs.append((v, first, close_ret))

    if not recs:
        print("표본 없음")
        return 2
    recs.sort(key=lambda r: r[0])
    n = len(recs)
    step = max(1, n // args.quintiles)
    print(f"표본 {n:,} 종목·일 · 목표 +{args.target}% / 손절 -{args.stop}%")
    print(f"{'분위':>4} {'ATR%구간':>14} {'목표먼저':>8} {'손절먼저':>8} {'미도달':>7} {'순기대(비용후)':>13}")
    for i in range(args.quintiles):
        chunk = recs[i * step:(i + 1) * step] if i < args.quintiles - 1 else recs[i * step:]
        if not chunk:
            continue
        hits = sum(1 for _, f, _ in chunk if f == "target")
        stops = sum(1 for _, f, _ in chunk if f == "stop")
        nones = len(chunk) - hits - stops
        close_avg = np.mean([cr for _, f, cr in chunk if f is None]) if nones else 0.0
        exp = (hits * args.target - stops * args.stop + nones * close_avg * 100) / len(chunk)
        lo, hi = chunk[0][0], chunk[-1][0]
        print(f"{i + 1:>4} {lo:6.2f}~{hi:6.2f}  {hits / len(chunk):7.1%} {stops / len(chunk):8.1%} "
              f"{nones / len(chunk):6.1%} {exp - 0.21:+12.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
