"""L5c 오프라인 증명 — 팩터 유니버스 필터의 '현재값 조회' 누수 크기 측정 (읽기 전용).

배경(코드 판독, 2026-10-07 02:0x 엔지니어 자율):
  services/strategy-agents/app/factors/universe.py::filter_universe 는
    ① storage.get_market_caps()            → stocks.market_cap (현재값, asof 미지원)
    ② storage.get_avg_trading_value(code, days=30)  → asof=None 위임 = '오늘 기준 최근 30행'
  두 축 모두 asof 시점을 무시한다 → 과거 리밸런싱 유니버스가 미래 정보를 쓴다.
  반면 PIT 게터는 이미 존재한다: postgres_storage.py:133 get_avg_trading_value_asof(asof_date=...).

이 스크립트는 그 누수가 유니버스를 얼마나 바꾸는지 '수치'로 만든다(승인 항목 L5c 근거).
무엇도 쓰지 않는다(SELECT only).

실행(컨테이너에 psycopg2 가 있고 DB 호스트명이 postgres):
  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_l5c_pit_universe_probe.py
"""
import os
import sys

import psycopg2
import psycopg2.extras

MIN_MARKET_CAP = 5e10          # universe.py MIN_MARKET_CAP
MIN_AVG_TRADING_VALUE = 1e9    # universe.py MIN_AVG_TRADING_VALUE
DAYS = 30

ASOF_DATES = ["2025-12-30", "2026-03-31", "2026-06-30", "2026-09-15"]

DSN = dict(
    host=os.environ.get("POSTGRES_HOST", "postgres"),
    port=int(os.environ.get("POSTGRES_PORT", "5432")),
    dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    user=os.environ.get("POSTGRES_USER", "stock_user"),
    password=os.environ.get("POSTGRES_PASSWORD", ""),
)

LAST_N_SQL = """
SELECT stock_code, trade_date, trading_value, close_price FROM (
  SELECT stock_code, trade_date, trading_value, close_price,
         ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY trade_date DESC) rn
  FROM market_data
  {where}
) t WHERE rn <= %s
"""


def fetch_last_n(cur, asof=None, n=DAYS):
    if asof is None:
        cur.execute(LAST_N_SQL.format(where=""), (n,))
    else:
        cur.execute(LAST_N_SQL.format(where="WHERE trade_date <= %s"), (asof, n))
    out = {}
    for r in cur.fetchall():
        code = r["stock_code"]
        d = out.setdefault(code, {"tv": [], "last_date": None, "last_close": None})
        if r["trading_value"] is not None:
            d["tv"].append(float(r["trading_value"]))
        if d["last_date"] is None or r["trade_date"] > d["last_date"]:
            d["last_date"] = r["trade_date"]
            d["last_close"] = float(r["close_price"]) if r["close_price"] is not None else None
    # tv 는 DESC 순으로 들어왔으므로 마지막 30행 평균
    return {c: {"tv_avg": (sum(v["tv"]) / len(v["tv"]) if v["tv"] else None),
                "last_date": v["last_date"], "last_close": v["last_close"]}
            for c, v in out.items()}


def main():
    conn = psycopg2.connect(**DSN)
    conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT stock_code, market_cap FROM stocks")
    caps_now = {r["stock_code"]: (float(r["market_cap"]) if r["market_cap"] is not None else None)
                for r in cur.fetchall()}

    now = fetch_last_n(cur, asof=None, n=DAYS)
    now_max = max((v["last_date"] for v in now.values() if v["last_date"]), default=None)
    print(f"[probe] DB now_max_trade_date = {now_max} · stocks={len(caps_now)} · market_data stocks w/30행={len(now)}")

    print("\n=== asof 별: '현재값 조회'(누수) vs 'PIT 조회' 유니버스 비교 ===")
    print("asof        | now전체 PIT전체 | 공집합 | now만(누수포함) | PIT만(누락) | 자카드")
    for asof in ASOF_DATES:
        pit = fetch_last_n(cur, asof=asof, n=DAYS)
        u_now, u_pit, both = [], [], 0
        leak_cap = leak_tv = 0
        for code, cap in caps_now.items():
            n = now.get(code)
            p = pit.get(code)
            if n is None:
                continue
            cap_now_ok = cap is not None and cap >= MIN_MARKET_CAP
            tv_now_ok = n["tv_avg"] is not None and n["tv_avg"] >= MIN_AVG_TRADING_VALUE
            if not (cap_now_ok and tv_now_ok):
                continue
            u_now.append(code)
            # PIT 시총 프록시: cap_now * close(asof)/close(now)
            cap_pit = None
            if cap is not None and p is not None and p["last_close"] and n["last_close"]:
                cap_pit = cap * (p["last_close"] / n["last_close"])
            cap_pit_ok = cap_pit is not None and cap_pit >= MIN_MARKET_CAP
            tv_pit_ok = p is not None and p["tv_avg"] is not None and p["tv_avg"] >= MIN_AVG_TRADING_VALUE
            if not cap_pit_ok:
                leak_cap += 1
            if not tv_pit_ok:
                leak_tv += 1
            if cap_pit_ok and tv_pit_ok:
                u_pit.append(code)
        both = len(set(u_now) & set(u_pit))
        only_now = len(set(u_now) - set(u_pit))
        only_pit = len(set(u_pit) - set(u_now))
        jac = both / max(1, len(set(u_now) | set(u_pit)))
        print(f"{asof} | {len(u_now):6d} {len(u_pit):6d} | {both:6d} | {only_now:6d} | {only_pit:6d} | {jac:.3f}"
              f"   (누수 축: 시총 {leak_cap}종목 · 거래대금 {leak_tv}종목)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
