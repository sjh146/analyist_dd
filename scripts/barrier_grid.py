"""손익비 격자 — 어떤 (손절, 목표) 조합이 실제로 플러스인지 분봉으로 직접 잰다.

일봉 근사는 '저가 터치=손절' 가정 때문에 고변동 종목을 과도하게 불리하게 만든다(2026-11-26 실측).
여기서는 분봉 첫 터치 순서로 계산하고, ATR% 분위별로 나눠 본다.

사용:
    python3 scripts/barrier_grid.py --min-days 20
"""
from __future__ import annotations

import argparse
import itertools
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
    ap.add_argument("--cost", type=float, default=0.21)
    ap.add_argument("--stop-slip", type=float, default=0.0,
                    help="손절 체결 슬리피지(%%p). 0.5%% 손절을 -0.8%%에 체결하는 현실 반영")
    ap.add_argument("--target-slip", type=float, default=0.0,
                    help="목표 체결 슬리피지(%%p, 지정가면 0에 가깝다)")
    ap.add_argument("--quintiles", type=int, default=5)
    ap.add_argument("--min-days", type=int, default=8)
    args = ap.parse_args()

    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code, trade_date::text, open_price, high_price, low_price, close_price
        FROM market_data WHERE open_price > 0
    """)
    daily, atr = {}, defaultdict(list)
    for c, d, o, h, l, cl in cur.fetchall():
        o, h, l, cl = float(o), float(h), float(l), float(cl)
        daily[(str(c), d)] = (o, cl)
        if cl > 0:
            atr[str(c)].append(((h - l) / cl * 100.0, d))
    cur.execute("""
        SELECT stock_code, trade_date::text, time, close_price
        FROM minute_bars_unified WHERE close_price > 0
        ORDER BY stock_code, trade_date, time
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    bars = defaultdict(list)
    for c, d, tm, px in rows:
        bars[(str(c), d)].append((int(tm), float(px)))
    vol = {}
    for code, series in atr.items():
        series.sort(key=lambda x: x[1])
        vals = [v for v, _ in series[-20:]]
        if vals:
            vol[code] = float(np.mean(vals))

    keys = [(k, v) for k, v in bars.items() if k in daily and k[0] in vol]
    print(f"표본 {len(keys):,} 종목·일 · 종목 {len({k[0] for k, _ in keys})}")

    stops = [0.005, 0.0075, 0.01, 0.015]
    targets = [0.01, 0.015, 0.02, 0.03, 0.05]
    results = {}
    for stop, target in itertools.product(stops, targets):
        payoffs = []
        for key, series in keys:
            o = daily[key][0]
            up = o * (1 + target)
            dn = o * (1 - stop)
            out = None
            for _, px in sorted(series):
                if px >= up:
                    out = target * 100 - args.target_slip
                    break
                if px <= dn:
                    out = -stop * 100 - args.stop_slip
                    break
            if out is None:
                last = sorted(series)[-1][1]
                out = (last / o - 1.0) * 100
            payoffs.append(out)
        arr = np.array(payoffs) - args.cost
        results[(stop, target)] = (arr.mean(), arr.std() / np.sqrt(len(arr)), len(arr))

    ranked = sorted(results.items(), key=lambda kv: -kv[1][0])[:8]
    print(f"\n{'손절':>6} {'목표':>6} {'순기대':>9} {'표준오차':>9} {'t':>6}")
    for (stop, target), (m, se, n) in ranked:
        print(f"{stop*100:5.2f}% {target*100:5.2f}% {m:+8.3f}% {se:8.3f}% {m/se:+6.2f}")

    rng = np.random.default_rng(7)
    best = ranked[0][0]
    boot = []
    for _ in range(200):
        idx = rng.integers(0, len(keys), len(keys))
        vals = []
        for i in idx:
            key, series = keys[i]
            o = daily[key][0]
            up = o * (1 + best[1])
            dn = o * (1 - best[0])
            out = None
            for _, px in sorted(series):
                if px >= up:
                    out = best[1] * 100 - args.target_slip
                    break
                if px <= dn:
                    out = -best[0] * 100 - args.stop_slip
                    break
            if out is None:
                out = (sorted(series)[-1][1] / o - 1.0) * 100
            vals.append(out)
        boot.append(np.mean(np.array(vals) - args.cost))
    boot = np.array(boot)
    print(f"\n최적 셀 {best[0]*100:.2f}%/{best[1]*100:.2f}% 부트스트랩 95% 구간: "
          f"{np.percentile(boot, 2.5):+.3f}% ~ {np.percentile(boot, 97.5):+.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
