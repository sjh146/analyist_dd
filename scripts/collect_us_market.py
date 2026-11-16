#!/usr/bin/env python3
"""미국 지수·환율·KOSPI200 야간 데이터 수집 → us_market_data (의존성 없음: requests + psycopg2).

왜 이 스크립트인가 (2026-11-16 실측):
  `yfinance-collector` 의 `run_830am_batch()` 는 `import yfinance` 를 하는데 requirements 에
  yfinance 가 없었고, 추가 후 이미지 빌드도 pypi ReadTimeout/BuildKit lease 오류로 실패했다.
  그래서 us_market_data 가 계속 0행이었다. 이 경로는 **컨테이너에 이미 있는 requests** 로
  Yahoo chart API 를 직접 호출해 같은 데이터를 채운다(새 의존성 0).

실행(컨테이너 안에서; DB 환경변수는 이미 설정돼 있다):
  docker exec stock_yfinance_collector python3 /app/scripts/collect_us_market.py
  docker exec stock_yfinance_collector python3 /app/scripts/collect_us_market.py --days 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

TICKERS = {
    "NASDAQ": "^IXIC",
    "SOX": "^SOX",
    "SP500": "^GSPC",
    "VIX": "^VIX",
    "USDKRW": "KRW=X",       # yfinance 의 USDKRW=X 와 동일 계열(KRW=X)
    "KOSPI200_NIGHT": "^KS200",  # KOSPI200 지수(야간선물 대용)
}
UA = "Mozilla/5.0 (compatible; analyist-dd/1.0)"
API = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"


def fetch(sym: str, days: int) -> list[tuple]:
    rng = f"{max(days, 5)}d"
    req = urllib.request.Request(API.format(sym=sym, rng=rng), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        payload = json.loads(r.read().decode())
    res = (payload.get("chart") or {}).get("result") or []
    if not res:
        return []
    res = res[0]
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    op = q.get("open") or []
    hi = q.get("high") or []
    lo = q.get("low") or []
    cl = q.get("close") or []
    vol = q.get("volume") or []
    out: list[tuple] = []
    for i, t in enumerate(ts):
        try:
            d = dt.datetime.fromtimestamp(int(t), dt.timezone.utc).date()
            close = cl[i] if i < len(cl) else None
            if close is None:
                continue

            def val(arr):
                return arr[i] if i < len(arr) and arr[i] is not None else None

            v = val(vol)
            out.append((d, float(close), val(op), val(hi), val(lo),
                        int(v) if v is not None else None))
        except Exception:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="가져올 일수(기본 7)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import psycopg2
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS us_market_data(
        id SERIAL PRIMARY KEY, trade_date DATE NOT NULL, index_name VARCHAR(20) NOT NULL,
        open_price DECIMAL(12,4), high_price DECIMAL(12,4), low_price DECIMAL(12,4),
        close_price DECIMAL(12,4), volume BIGINT, created_at TIMESTAMP DEFAULT NOW())""")
    cur.execute("SELECT index_name, trade_date FROM us_market_data")
    have = set(cur.fetchall())

    total = 0
    for name, sym in TICKERS.items():
        try:
            rows = fetch(sym, args.days)
        except Exception as e:  # noqa: BLE001
            print(f"[us] {name} ({sym}) fetch 실패: {e}", file=sys.stderr)
            continue
        new = [r for r in rows if (name, r[0]) not in have]
        for d, close, op, hi, lo, vol in new:
            if args.dry_run:
                continue
            cur.execute(
                "INSERT INTO us_market_data(trade_date,index_name,open_price,high_price,low_price,close_price,volume)"
                " VALUES(%s,%s,%s,%s,%s,%s,%s)", (d, name, op, hi, lo, close, vol))
        total += len(new)
        print(f"[us] {name:15s} {sym:8s} 조회 {len(rows):2d}일 · 신규 {len(new):2d}건 · 최신 {rows[-1][0] if rows else '-'}")
    if not args.dry_run:
        conn.commit()
    cur.close()
    conn.close()
    print(f"[us] 총 신규 {total}건 {'(dry-run)' if args.dry_run else '적재 완료'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
