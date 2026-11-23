#!/usr/bin/env python3
"""실효 왕복비용 측정 — 후보 종목별 스프레드·레인지·거래대금으로 '스캘핑이 성립하는지' 판단.

WHY(2026-11-23): 스캘핑/단타의 성패는 방향 엣지가 아니라 **비용 대비 변동폭**이다.
   지금까지 쓰던 왕복비용 0.21%(수수료+거래세, 실체결 검증)는 **스프레드를 포함하지 않는다**.
   작은 종목의 호가 스프레드는 실측 0.1~1.1% 로, 그 자체가 라인(1%/3%)보다 큰 경우가 있다.

측정:
   ① 브리지 /quotes (bid1·offer1·high·low·base·amount) — WSL 에서는 Windows 루프백이 막히므로
      **curl.exe(Windows 프로세스)** 로 조회한다(스킬 함정: --noproxy 필수).
   ② DB market_data 5거래일 — 일중 레인지%((high-low)/close)와 평균 거래대금.
   ③ 실효 왕복비용 ≈ 0.21%(수수료+세금) + 스프레드(진입 반 + 청산 반 ≈ 1회 스프레드).

출력: reports/cost_structure_<YYYYMMDD>.json + 콘솔 표.
사용: python3 scripts/cost_structure_probe.py [--codes 005930,...] [--top 20]
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import date

import psycopg2

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = os.path.join(PROJ, "data", "feed", "screener_latest.json")
CURL = "/mnt/c/Windows/System32/curl.exe"
BRIDGE = "http://127.0.0.1:8100"
BASE_COST_PCT = 0.21          # 수수료+거래세 (실체결 검증, 스프레드 제외)


def quotes(codes):
    url = f"{BRIDGE}/quotes?codes={','.join(codes)}"
    out = subprocess.run([CURL, "-s", "--noproxy", "*", "-m", "20", url],
                         capture_output=True, text=True).stdout
    try:
        return (json.loads(out) or {}).get("quotes") or {}
    except ValueError:
        sys.stderr.write(f"[cost] quotes 파싱 실패: {out[:200]}\n")
        return {}


def db_stats(codes):
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT stock_code, count(*),
                          avg((high_price - low_price) / NULLIF(close_price,0)) * 100,
                          avg(trading_value)
                   FROM market_data
                   WHERE stock_code = ANY(%s) AND trade_date >= CURRENT_DATE - 14
                   GROUP BY 1""", (list(codes),))
            return {r[0]: {"days": int(r[1]), "range_pct": float(r[2] or 0),
                           "avg_value": float(r[3] or 0)} for r in cur.fetchall()}
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        d = json.load(open(FEED, encoding="utf-8"))
        items = ((d.get("candidates") or {}).get("daytrading") or {}).get("items") or []
        codes = [i["stock_code"] for i in items[: args.top]]

    q = quotes(codes)
    st = db_stats(codes)
    rows = []
    for c in codes:
        x = q.get(c) or {}
        bid, offer = x.get("bid1") or 0, x.get("offer1") or 0
        mid = (bid + offer) / 2 if bid and offer else 0
        spread_pct = ((offer - bid) / mid * 100) if mid else None
        hi, lo, base = x.get("high") or 0, x.get("low") or 0, x.get("base") or 0
        day_range_pct = ((hi - lo) / base * 100) if base and hi and lo else None
        s = st.get(c, {})
        rng5 = s.get("range_pct")
        cost = (BASE_COST_PCT + spread_pct) if spread_pct is not None else None
        rows.append({
            "code": c, "name": x.get("name"), "price": x.get("current"),
            "bid1": bid, "offer1": offer, "spread_pct": round(spread_pct, 3) if spread_pct is not None else None,
            "day_range_pct": round(day_range_pct, 2) if day_range_pct is not None else None,
            "range5_pct": round(rng5, 2) if rng5 else None,
            "avg_value_5d_eok": round((s.get("avg_value") or 0) / 1e8, 1),
            "roundtrip_cost_pct": round(cost, 3) if cost is not None else None,
            "range_over_cost": (round(rng5 / cost, 1) if (rng5 and cost) else None),
        })

    out = {"measured_at": date.today().isoformat(), "base_cost_pct": BASE_COST_PCT,
           "rows": rows}
    path = os.path.join(PROJ, "reports", f"cost_structure_{date.today():%Y%m%d}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"{'code':<8}{'name':<14}{'spread%':>8}{'오늘레인지%':>10}{'5d레인지%':>10}{'거래대금억':>10}{'왕복비용%':>10}{'레인지/비용':>11}")
    for r in sorted(rows, key=lambda z: (z["roundtrip_cost_pct"] or 99)):
        print(f"{r['code']:<8}{(r['name'] or '')[:12]:<14}"
              f"{(r['spread_pct'] if r['spread_pct'] is not None else -1):>8.3f}"
              f"{(r['day_range_pct'] or 0):>10.2f}{(r['range5_pct'] or 0):>10.2f}"
              f"{r['avg_value_5d_eok']:>10.1f}"
              f"{(r['roundtrip_cost_pct'] or -1):>10.3f}"
              f"{(r['range_over_cost'] or 0):>11.1f}")

    ok = [r for r in rows if r["roundtrip_cost_pct"] is not None and r["roundtrip_cost_pct"] <= 0.40]
    print(f"\n스프레드 조회 {len([r for r in rows if r['spread_pct'] is not None])}/{len(rows)}종목 · "
          f"왕복비용 0.40% 이하 {len(ok)}종목")
    print(f"저장: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
