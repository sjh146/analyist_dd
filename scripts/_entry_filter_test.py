#!/usr/bin/env python3
"""진입 필터 실측 — '공시·재무' 근거로 걸러내면 실제로 손실이 줄어드는가?

필터 후보(모두 as-of: 진입일 이전 데이터만):
  D1 희석성 공시 14일 내  (유상증자·전환사채·신주인수권·감자·유무상증자)
  D2 시장경고성 공시 30일 내 (관리종목·투자주의·불성실공시·상장폐지)
  F1 최근 분기 ROE < 0
  F2 최근 분기 부채비율 > 200%
비교 지표: 익일 시가→종가 수익률(수수료 0.21% 차감), 그리고 손절 2%/익절 2% 적용 시 중립가정 손익.
"""
import os

import numpy as np
import pandas as pd
import psycopg2

FEE = 0.0021
START, END = "2026-04-01", "2026-11-14"
DILUTE = "유상증자|전환사채|신주인수권|유무상증자|감자|제3자배정"
WARN = "관리종목|투자주의|불성실공시|상장폐지|거래정지"


def conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=os.environ.get("POSTGRES_PORT", "5434"),
        user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"))


def main() -> None:
    con = conn()
    df = pd.read_sql("""
        SELECT stock_code, trade_date, open_price, high_price, low_price, close_price,
               volume, trading_value
        FROM market_data WHERE trade_date BETWEEN %s AND %s
          AND close_price >= 1000 AND open_price > 0""", con, params=(START, END))
    for c in ["open_price", "high_price", "low_price", "close_price", "volume", "trading_value"]:
        df[c] = df[c].astype(float)
    df = df.sort_values(["stock_code", "trade_date"])
    g = df.groupby("stock_code")
    df["chg"] = g["close_price"].pct_change() * 100
    df["vol_ratio"] = df["volume"] / g["volume"].transform(lambda s: s.rolling(20).mean())
    df["nxt_open"] = g["open_price"].shift(-1)
    df["nxt_high"] = g["high_price"].shift(-1)
    df["nxt_low"] = g["low_price"].shift(-1)
    df["nxt_close"] = g["close_price"].shift(-1)
    df["nxt_date"] = g["trade_date"].shift(-1)
    df = df.dropna(subset=["nxt_open", "nxt_close", "nxt_date"])
    df = df[(df["nxt_open"] > 0) & (df["nxt_high"] > 0) & (df["nxt_low"] > 0)]

    sig = df[(df["chg"] >= 3.0) & (df["vol_ratio"] >= 1.5) & (df["trading_value"] >= 5e8)].copy()
    print(f"신호 {len(sig)}건 · 종목 {sig['stock_code'].nunique()}")

    dis = pd.read_sql("SELECT stock_code, rcept_dt::date AS d, report_nm FROM disclosures", con)
    dis["dilute"] = dis["report_nm"].str.contains(DILUTE, na=False)
    dis["warn"] = dis["report_nm"].str.contains(WARN, na=False)
    print(f"공시 {len(dis)}건 ({dis['d'].min()}~{dis['d'].max()}) · 희석성 {int(dis['dilute'].sum())} · 경고성 {int(dis['warn'].sum())}")

    fin = pd.read_sql("SELECT stock_code, report_date::date AS d, roe, debt_ratio, per FROM financial_statements", con)
    print(f"재무 {len(fin)}건 ({fin['d'].min()}~{fin['d'].max()})")

    def flag_table(kind: str, lookback: int) -> pd.Series:
        sub = dis[dis[kind]].copy()
        sub["lo"] = sub["d"] - pd.Timedelta(days=lookback)
        keys = set()
        for code, lo, d in sub[["stock_code", "lo", "d"]].itertuples(index=False):
            keys.add((code, lo, d))
        out = []
        for code, day in zip(sig["stock_code"], sig["nxt_date"]):
            hit = any(code == c and lo <= day <= d for c, lo, d in keys)
            out.append(hit)
        return pd.Series(out, index=sig.index)

    def fin_flag(cond) -> pd.Series:
        rows = []
        fin_sorted = fin.sort_values("d")
        for code, day in zip(sig["stock_code"], sig["nxt_date"]):
            sub = fin_sorted[(fin_sorted["stock_code"] == code) & (fin_sorted["d"] <= day)]
            if sub.empty:
                rows.append(np.nan)
            else:
                rows.append(bool(cond(sub.iloc[-1])))
        return pd.Series(rows, index=sig.index, dtype="float")

    sig["dilute"] = flag_table("dilute", 14)
    sig["warn"] = flag_table("warn", 30)
    sig["roe_neg"] = fin_flag(lambda r: pd.notna(r["roe"]) and float(r["roe"]) < 0)
    sig["debt_hi"] = fin_flag(lambda r: pd.notna(r["debt_ratio"]) and float(r["debt_ratio"]) > 200)

    sig["ret"] = (sig["nxt_close"] / sig["nxt_open"] - 1 - FEE) * 100
    # 2%/2% 중립가정(순서 50:50) 손익
    e, hi, lo, cl = (sig[c].values for c in ("nxt_open", "nxt_high", "nxt_low", "nxt_close"))
    tgt, stp = e * 1.02, e * 0.98
    both = (hi >= tgt) & (lo <= stp)
    up_only = (hi >= tgt) & ~both
    dn_only = (lo <= stp) & ~both
    mid = np.where(both, 0.0, np.where(up_only, 0.02, np.where(dn_only, -0.02, cl / e - 1)))
    sig["r2"] = mid * 100 - FEE * 100

    print(f"\n[전체] n={len(sig)} · 시가→종가 평균 {sig['ret'].mean():+.3f}% · 2%/2% 중립 {sig['r2'].mean():+.3f}%")

    for name, col in [("희석성 공시 14일", "dilute"), ("경고성 공시 30일", "warn"),
                      ("ROE<0", "roe_neg"), ("부채비율>200%", "debt_hi")]:
        hit = sig[sig[col] == 1]
        miss = sig[sig[col] == 0]
        unknown = int(sig[col].isna().sum())
        if len(hit) == 0:
            print(f"  {name:<16} 대상 0건")
            continue
        print(f"  {name:<16} 차단 {len(hit):>5}건 평균 {hit['ret'].mean():+.3f}% / 2%중립 {hit['r2'].mean():+.3f}% "
              f"|| 통과 {len(miss):>5}건 평균 {miss['ret'].mean():+.3f}% / {miss['r2'].mean():+.3f}%"
              f"  Δ(통과-차단) {miss['ret'].mean() - hit['ret'].mean():+.3f}%p (미상 {unknown})")

    for combo in (["dilute"], ["warn"], ["dilute", "warn"], ["dilute", "warn", "roe_neg"]):
        blocked = sig[sig[combo].fillna(0).astype(int).sum(axis=1) > 0]
        kept = sig[sig[combo].fillna(0).astype(int).sum(axis=1) == 0]
        print(f"  조합 {'+'.join(combo):<24} 차단 {len(blocked):>5}건({len(blocked)/len(sig)*100:.1f}%) "
              f"차단분 평균 {blocked['ret'].mean() if len(blocked) else 0:+.3f}% · "
              f"남은 {len(kept):>5}건 평균 {kept['ret'].mean():+.3f}% → 개선 {kept['ret'].mean() - sig['ret'].mean():+.3f}%p")

    con.close()


if __name__ == "__main__":
    main()
