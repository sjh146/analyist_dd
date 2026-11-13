#!/usr/bin/env python3
"""liq_slippage_probe.py — '저거래대금' 신호의 Δ(+1.20%p)가 실제 체결 비용 안에서 사는가.

WHY (2026-11-13, CG254 note):
  CG253 은 SNS 풀에서 거래대금 하위 top-k 가 같은 날 풀평균을 Δ+1.20%p(t 3.38) 앞선다고 쟀다.
  그런데 슬리피지 0.5%p 는 **가정값**이었고(호가 스프레드 미실측), 하한 30억 종목이 실제로
  얼마나 흔들리는지는 아무도 안 쟀다. minute_bars 는 지금 15:01~15:30 구간만 있지만(수집기
  페이지네이션 결함, XR26) **종가 진입 전략이 체결되는 창이 바로 그 구간**이다 → 이 창의
  고저폭을 '체결 비용 상한 프록시'로 쓸 수 있다.

계산 규칙:
  · 같은 거래일 안에서 종목들을 거래대금 10분위로 나눈다(날짜 고정효과 제거).
  · range_pct = (30분창 max(high) − min(low)) / 그날 종가 × 100  — 진입~청산 사이 최악 흔들림.
  · slip_pct  = |30분창 마지막 종가 − 일봉 종가| / 일봉 종가 × 100 — 종가 집행 오차 프록시.
  · 판정: D1(최저 거래대금)의 중앙 range_pct 가 신호 크기(기본 1.20%p) 이상이면 '규칙은 비용에
    먹힌다'. 그 미만이면 '비용 여유 있음'.

사용(컨테이너, cwd=/app):
  python -u scripts/liq_slippage_probe.py --days 20 --edge 1.20 --json-out /app/reports/overnight/cg259_liq_slip.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
from collections import defaultdict

import psycopg2


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--edge", type=float, default=1.20)
    ap.add_argument("--buckets", type=int, default=10)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    con = connect()
    cur = con.cursor()
    cur.execute(
        """
        WITH win AS (
          SELECT stock_code::text AS code, trade_date,
                 max(high_price) AS hi, min(low_price) AS lo,
                 (array_agg(close_price ORDER BY time DESC))[1] AS last_close,
                 sum(trading_value) AS win_value
          FROM minute_bars
          WHERE trade_date >= (CURRENT_DATE - make_interval(days => %s))
            AND time >= '150100' AND time <= '153000'
            AND close_price IS NOT NULL AND close_price > 0
          GROUP BY stock_code, trade_date
        )
        SELECT w.code, w.trade_date, w.hi, w.lo, w.last_close, w.win_value,
               m.close_price, m.high_price, m.low_price, m.trading_value
        FROM win w
        JOIN market_data m ON m.stock_code = w.code AND m.trade_date = w.trade_date
        WHERE m.close_price > 0 AND m.trading_value IS NOT NULL
        """,
        (a.days,),
    )
    rows = cur.fetchall()
    con.close()

    by_date = defaultdict(list)
    for code, d, hi, lo, last_close, win_value, close, dhi, dlo, tv in rows:
        hi, lo, close, last_close = float(hi), float(lo), float(close), float(last_close)
        if close <= 0 or hi < lo:
            continue
        by_date[str(d)].append({
            "tv": float(tv),
            "range_pct": (hi - lo) / close * 100.0,
            "slip_pct": abs(last_close - close) / close * 100.0,
            "win_value_share": (float(win_value) / float(tv) * 100.0) if tv else None,
        })

    nb = a.buckets
    per_bucket = {i: {"range": [], "slip": [], "share": [], "tv": []} for i in range(nb)}
    for d, lst in by_date.items():
        lst.sort(key=lambda r: r["tv"])
        n = len(lst)
        if n < nb * 3:
            continue
        for idx, r in enumerate(lst):
            b = min(nb - 1, idx * nb // n)  # 0 = 최저 거래대금
            per_bucket[b]["range"].append(r["range_pct"])
            per_bucket[b]["slip"].append(r["slip_pct"])
            if r["win_value_share"] is not None:
                per_bucket[b]["share"].append(r["win_value_share"])
            per_bucket[b]["tv"].append(r["tv"])

    out = {"days": len(by_date), "rows": len(rows), "edge_pct": a.edge,
           "note": "range_pct=15:01~15:30 고저폭/종가 · slip_pct=|창마지막종가−일봉종가|/종가", "buckets": {}}
    print(f"[scope] dates={len(by_date)} rows={len(rows)}")
    print(f"{'bucket':>7} {'n':>7} {'tv_med(억)':>10} {'range_med%':>11} {'range_p75':>10} {'slip_med%':>10} {'winval%':>8}")
    for b in range(nb):
        p = per_bucket[b]
        if not p["range"]:
            continue
        rec = {
            "n": len(p["range"]),
            "tv_median_krw": round(st.median(p["tv"]), 0),
            "range_pct_median": round(st.median(p["range"]), 3),
            "range_pct_p75": round(st.quantiles(p["range"], n=4)[2], 3) if len(p["range"]) > 3 else None,
            "slip_pct_median": round(st.median(p["slip"]), 3),
            "close_window_value_share_pct": round(st.median(p["share"]), 2) if p["share"] else None,
        }
        out["buckets"][f"D{b+1}"] = rec
        print(f"{'D'+str(b+1):>7} {rec['n']:>7} {rec['tv_median_krw']/1e8:>10.1f} "
              f"{rec['range_pct_median']:>11} {str(rec['range_pct_p75']):>10} {rec['slip_pct_median']:>10} "
              f"{str(rec['close_window_value_share_pct']):>8}")

    d1 = out["buckets"].get("D1")
    dall = [v for b in per_bucket.values() for v in b["range"]]
    if d1 and dall:
        out["verdict"] = {
            "D1_range_median_pct": d1["range_pct_median"],
            "all_range_median_pct": round(st.median(dall), 3),
            "ratio": round(d1["range_pct_median"] / st.median(dall), 2) if st.median(dall) else None,
            "edge_pct": a.edge,
            "survives": bool(d1["range_pct_median"] < a.edge),
        }
        print("[verdict]", json.dumps(out["verdict"], ensure_ascii=False))

    if a.json_out:
        with open(a.json_out, "w") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)
        print("저장:", a.json_out)


if __name__ == "__main__":
    main()
