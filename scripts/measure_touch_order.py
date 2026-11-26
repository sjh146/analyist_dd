#!/usr/bin/env python3
"""손절/목표 도달 '순서' 실측 — 일봉 실험의 '손절 먼저' 가정을 검증한다.

일봉만으로는 장중에 목표(+x%)와 손절(-y%) 중 무엇이 먼저 닿았는지 알 수 없다.
분봉 종가가 있으면(현재 11-10~11-24, 837종목) 첫 터치 시각을 비교해 비율을 잴 수 있다.

사용:
    python3 scripts/measure_touch_order.py --target 3.0 --stop 1.0
    python3 scripts/measure_touch_order.py --target 1.5 --stop 1.0
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict

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
    args = ap.parse_args()

    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code, trade_date::text, open_price
        FROM market_data
        WHERE trade_date >= '2026-11-10' AND open_price > 0
    """)
    opens = {(str(c), d): float(o) for c, d, o in cur.fetchall()}
    cur.execute("""
        SELECT stock_code, trade_date::text, time, close_price
        FROM minute_close_bars
        WHERE close_price > 0
        ORDER BY stock_code, trade_date, time
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    bars = defaultdict(list)
    for code, day, tm, px in rows:
        bars[(str(code), day)].append((int(tm), float(px)))

    tgt, stp = args.target / 100.0, args.stop / 100.0
    stats = defaultdict(int)
    close_sum = 0.0
    close_n = 0
    for key, series in bars.items():
        o = opens.get(key)
        if not o:
            continue
        up, dn = o * (1 + tgt), o * (1 - stp)
        ordered = sorted(series)
        first = None
        for tm, px in ordered:
            if px >= up:
                first = "target"
                break
            if px <= dn:
                first = "stop"
                break
        stats[first] += 1
        if first is None and ordered:
            close_sum += ordered[-1][1] / o - 1.0
            close_n += 1

    total = sum(stats.values())
    if not total:
        print("분봉 표본 없음")
        return 2
    hit_first, stop_first, neither = stats["target"], stats["stop"], stats[None]
    both = hit_first + stop_first
    print(f"표본 {total:,} 종목·일 (목표 +{args.target}% / 손절 -{args.stop}%)")
    print(f"  목표 먼저 {hit_first:,} ({hit_first / total:.1%}) · 손절 먼저 {stop_first:,} "
          f"({stop_first / total:.1%}) · 미도달 {neither:,} ({neither / total:.1%})")
    if both:
        print(f"  둘 다 터치한 날 중 목표 먼저 {hit_first / both:.1%} / 손절 먼저 {stop_first / both:.1%}")
    close_avg = (close_sum / close_n * 100.0) if close_n else 0.0
    exp = (hit_first * args.target - stop_first * args.stop + neither * close_avg) / total
    print(f"  미도달 {neither:,}건은 종가청산({close_avg:+.3f}%)로 계산")
    print(f"  순서 반영 기대값(비용 전) {exp:+.3f}% · 비용 0.21% 차감 {exp - 0.21:+.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
