#!/usr/bin/env python3
"""손절/익절 폭 그리드 실측 — '1~2% 단타'가 실제로 수수료를 넘는지 (일봉 OHLC 근사).

한계(정직하게): 일봉에는 장중 **순서**가 없다. 그래서 각 거래일을 두 극단으로 계산한다.
  · worst: 저가 먼저 닿았다고 가정 → 손절 우선
  · best : 고가 먼저 닿았다고 가정 → 익절 우선
두 값의 차이가 곧 '순서 불확실성'이며, 결론은 이 구간(band)으로 보고해야 한다.

진입 규칙(전략 근사): 신호일 t 에 유동성(거래대금 5억↑) + 당일 +3%↑ + 거래량 1.5배↑ 종목을
t+1 **시가**에 매수. 청산은 (a) 당일 고가/저가 터치 (b) 미터치 시 당일 종가.
"""
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

FEE = 0.0021          # 왕복 비용(수수료+거래세) 0.21% — 증권사 실제 체결로 검증된 값
START, END = "2026-04-01", "2026-11-14"


def load() -> pd.DataFrame:
    con = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=os.environ.get("POSTGRES_PORT", "5434"),
        user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"))
    q = """
        SELECT stock_code, trade_date, open_price, high_price, low_price, close_price,
               volume, trading_value
        FROM market_data
        WHERE trade_date BETWEEN %s AND %s AND close_price >= 1000 AND open_price > 0
    """
    df = pd.read_sql(q, con, params=(START, END))
    con.close()
    for c in ["open_price", "high_price", "low_price", "close_price", "volume", "trading_value"]:
        df[c] = df[c].astype(float)
    df = df.sort_values(["stock_code", "trade_date"])
    g = df.groupby("stock_code")
    df["chg"] = g["close_price"].pct_change() * 100
    df["vol20"] = g["volume"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = df["volume"] / df["vol20"]
    df["nxt_open"] = g["open_price"].shift(-1)
    df["nxt_high"] = g["high_price"].shift(-1)
    df["nxt_low"] = g["low_price"].shift(-1)
    df["nxt_close"] = g["close_price"].shift(-1)
    df["nxt_date"] = g["trade_date"].shift(-1)
    # 5세션 보유(스윙 비교군) 수익률
    df["fwd5_close"] = g["close_price"].shift(-5)
    df = df[(df["nxt_open"] > 0) & (df["nxt_high"] > 0) & (df["nxt_low"] > 0)]
    return df.dropna(subset=["nxt_open", "nxt_high", "nxt_low", "nxt_close"])


def simulate(df: pd.DataFrame, stop: float, tp: float, same_day: bool = True) -> dict:
    """stop/tp 는 % (양수). 반환: worst/best 시나리오 기대손익(수수료 후, %)."""
    e = df["nxt_open"].values
    hi = df["nxt_high"].values
    lo = df["nxt_low"].values
    cl = df["nxt_close"].values
    up, dn = tp / 100.0, stop / 100.0
    tgt, stp = e * (1 + up), e * (1 - dn)

    r = {}
    for mode, stop_first in (("worst", True), ("best", False)):
        hit_tp = hi >= tgt
        hit_sl = lo <= stp
        both = hit_tp & hit_sl
        if stop_first:
            ret = np.where(hit_sl, -dn, np.where(hit_tp, up, cl / e - 1))
        else:
            ret = np.where(hit_tp, up, np.where(hit_sl, -dn, cl / e - 1))
        net = ret - FEE
        r[mode] = {
            "n": int(len(ret)),
            "win": float((net > 0).mean() * 100),
            "avg": float(net.mean() * 100),
            "sum": float(net.sum() * 100),
            "median": float(np.median(net) * 100),
            "ambiguous": float(both.mean() * 100),
            "tp_rate": float(hit_tp.mean() * 100),
            "sl_rate": float(hit_sl.mean() * 100),
            "whipsaw": float(((lo <= stp) & (cl > e)).mean() * 100),
        }
    return r


def main() -> None:
    df = load()
    sig = df[(df["chg"] >= 3.0) & (df["vol_ratio"] >= 1.5) & (df["trading_value"] >= 5e8)].copy()
    sig = sig.dropna(subset=["nxt_open"])
    print(f"유니버스 {df['stock_code'].nunique()}종목 / 기간 {START}~{END} / 신호 {len(sig)}건")
    print(f"비용 가정: 왕복 {FEE*100:.2f}%\n")

    base = (sig["nxt_close"] / sig["nxt_open"] - 1 - FEE).values * 100
    print(f"[기준선] 익일 시가→종가 (손절/익절 없음): 평균 {base.mean():+.3f}% · 승률 {(base>0).mean()*100:.1f}% · 합계 {base.sum():+.1f}%p")
    sw5 = (sig["fwd5_close"] / sig["nxt_open"] - 1 - FEE).dropna().values * 100
    print(f"[비교: 5세션 스윙 보유]            : 평균 {sw5.mean():+.3f}% · 승률 {(sw5>0).mean()*100:.1f}% · 합계 {sw5.sum():+.1f}%p (n={len(sw5)})\n")

    print(f"{'손절/익절':>10} | {'worst 평균':>10} {'worst 승률':>9} {'best 평균':>9} {'best 승률':>8} | {'손절터치':>7} {'익절터치':>7} {'되돌림(손절후종가↑)':>8} | {'중립가정':>7}")
    rows = []
    for stop, tp in [(1, 1), (1.5, 1.5), (2, 2), (1, 2), (2, 3), (3, 3), (5, 5), (7, 7)]:
        r = simulate(sig, stop, tp)
        w, b = r["worst"], r["best"]
        mid = 0.5 * (w["avg"] + b["avg"])
        print(f"{stop:>4.1f}%/{tp:<4.1f}% | {w['avg']:>+10.3f} {w['win']:>8.1f}% {b['avg']:>+9.3f} {b['win']:>7.1f}% | "
              f"{w['sl_rate']:>6.1f}% {w['tp_rate']:>6.1f}% {w['whipsaw']:>10.1f}% | {mid:>+7.3f}%")
        rows.append({"stop": stop, "tp": tp, **{f"w_{k}": v for k, v in w.items()},
                     **{f"b_{k}": v for k, v in b.items()}})

    # 손익분기 승률(수수료 포함)
    print("\n[손익분기 승률] stop=tp=s, 비용 c=0.21% → p* = (s+c)/(2s)")
    for s in (1.0, 1.5, 2.0, 3.0, 5.0, 7.0):
        print(f"  ±{s:.1f}% → 필요 승률 {(s + FEE*100) / (2 * s) * 100:.1f}%")

    out = pd.DataFrame(rows)
    os.makedirs("reports", exist_ok=True)
    out.to_csv("reports/stoploss_grid.csv", index=False)
    print("\n저장: reports/stoploss_grid.csv")


if __name__ == "__main__":
    main()
