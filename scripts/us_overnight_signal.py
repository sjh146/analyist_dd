"""미장 오버나이트 신호 — 나스닥·SOX 움직임을 다음 한국장 참조값으로 만들고, 실제 효력이 있는지 잰다.

두 가지를 한다.
  1) 산출: 최근 미국 세션의 NASDAQ/SOX/VIX 변화율과 60일 z점수 → data/state/us_overnight.json
     (07:40 수집 → 08:20 프리오픈/피드가 참조)
  2) 검증: 그 신호가 다음 한국장의 (a) KOSDAQ 시가→종가, (b) 단타 목표도달률/기대값을
     실제로 가르는지 측정한다. 측정 없이 게이트로 쓰지 않는다.

사용:
    python3 scripts/us_overnight_signal.py            # 산출 + 검증
    python3 scripts/us_overnight_signal.py --write    # 상태 파일까지 저장
"""
from __future__ import annotations

import argparse
import json
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


def load_series(cur, index_name):
    cur.execute("""
        SELECT trade_date, close_price FROM us_market_data
        WHERE index_name = %s AND close_price > 0 ORDER BY trade_date
    """, (index_name,))
    rows = cur.fetchall()
    days = [str(r[0]) for r in rows]
    vals = [float(r[1]) for r in rows]
    return days, vals


def returns(days, vals):
    out = {}
    for i in range(1, len(vals)):
        if vals[i - 1] > 0:
            out[days[i]] = (vals[i] / vals[i - 1] - 1.0) * 100.0
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--state", default="data/state/us_overnight.json")
    args = ap.parse_args()

    conn = pg_connect()
    cur = conn.cursor()
    series = {}
    for name in ("NASDAQ", "SOX", "SP500", "VIX"):
        days, vals = load_series(cur, name)
        series[name] = (days, vals, returns(days, vals))

    latest = {}
    for name, (days, vals, rets) in series.items():
        if not rets:
            continue
        d = max(rets)
        hist = [v for k, v in sorted(rets.items())][-60:]
        mu = float(np.mean(hist))
        sd = float(np.std(hist)) or 1.0
        latest[name] = {"date": d, "ret_pct": round(rets[d], 3),
                        "z": round((rets[d] - mu) / sd, 2), "mu60": round(mu, 3), "sd60": round(sd, 3)}

    sox_z = latest.get("SOX", {}).get("z", 0.0)
    nas_z = latest.get("NASDAQ", {}).get("z", 0.0)
    tilt = "neutral"
    if sox_z >= 1.0 or nas_z >= 1.0:
        tilt = "risk_on"
    elif sox_z <= -1.0 or nas_z <= -1.0:
        tilt = "risk_off"

    payload = {"generated_at": None, "tilt": tilt, "signals": latest}
    cur.execute("select now()")
    payload["generated_at"] = cur.fetchone()[0].isoformat()

    print("== 최근 미국 세션 ==")
    for name, info in latest.items():
        print(f"  {name:7s} {info['date']} {info['ret_pct']:+.2f}%  z {info['z']:+.2f}")

    cur.execute("""
        SELECT trade_date::text, open::float8, close::float8
        FROM index_daily WHERE index_code = 'KOSDAQ' AND open > 0 ORDER BY trade_date
    """)
    kosdaq = [(d, o, c) for d, o, c in cur.fetchall()]

    cur.execute("""
        SELECT stock_code, trade_date::text, time, close_price
        FROM minute_bars_unified WHERE close_price > 0 ORDER BY stock_code, trade_date, time
    """)
    bars = defaultdict(list)
    for c, d, t, p in cur.fetchall():
        bars[(str(c), d)].append((int(t), float(p)))
    cur.execute("select stock_code, trade_date::text, open_price from market_data where open_price > 0")
    opens = {(str(c), d): float(o) for c, d, o in cur.fetchall()}
    conn.close()

    sox_days, sox_vals, sox_rets = series["SOX"]
    sox_hist = sorted(sox_rets.items())[-60:]
    mu = float(np.mean([v for _, v in sox_hist])) if sox_hist else 0.0
    sd = float(np.std([v for _, v in sox_hist])) or 1.0

    buckets = defaultdict(lambda: {"days": [], "touch": [0, 0]})
    for day, o, c in kosdaq:
        prior = [d for d in sox_rets if d < day]
        if not prior:
            continue
        z = (sox_rets[max(prior)] - mu) / sd
        bucket = "z<=-1" if z <= -1 else ("z>=1" if z >= 1 else "-1<z<1")
        buckets[bucket]["days"].append((c / o - 1.0) * 100)

    for (code, day), series_bars in bars.items():
        prior = [d for d in sox_rets if d < day]
        if not prior:
            continue
        z = (sox_rets[max(prior)] - mu) / sd
        bucket = "z<=-1" if z <= -1 else ("z>=1" if z >= 1 else "-1<z<1")
        o = opens.get((code, day))
        if not o:
            continue
        up = o * 1.05
        hit = 1 if any(px >= up for _, px in series_bars) else 0
        buckets[bucket]["touch"][0] += hit
        buckets[bucket]["touch"][1] += 1

    print("\n== SOX 오버나이트 z → 다음 한국장 ==")
    print(f"{'구간':>10} {'KOSDAQ 시가→종가':>16} {'5% 도달률':>10} {'일수':>5}")
    for bucket in ("z<=-1", "-1<z<1", "z>=1"):
        info = buckets.get(bucket)
        if not info:
            continue
        r = info["days"]
        t = info["touch"]
        mean_r = float(np.mean(r)) if r else 0.0
        hit = (t[0] / t[1]) if t[1] else 0.0
        print(f"{bucket:>10} {mean_r:+15.3f}% {hit:9.1%} {len(r):5d}")

    if args.write:
        os.makedirs(os.path.dirname(args.state), exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print(f"\n저장: {args.state} (tilt={tilt})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
