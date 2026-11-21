#!/usr/bin/env python3
"""스크리너 구성요소 진단 — 왜 이 종목들을 사면 초과수익이 음인가.

WHY(2026-11-20): daytrading 점수는 **칼만30 + 모델30 + 거래대금20 + 변동성20** 복합인데,
룰 실측에서 추격 계열이 전부 마이너스 초과수익이었다. 어느 구성요소가 음을 만드는지
구성요소별로 분해해 본다. (모델 점수는 재현이 어려워 DB 에서 계산 가능한 대리변수로 한다:
ret1/ret3/ret5/ret20, 거래대금비, ATR%, 종가강도)

핵심 측정: **일별 횡단면 초과수익**(같은 날 유니버스 평균을 뺀 값). 시장 드리프트를 제거해야
구성요소의 실제 예측력이 보인다(대조군 없는 IC 금지 원칙).

사용:
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 python3 scripts/screener_component_ic.py \
      --from 2026-01-02 --to 2026-11-19 --json-out data/reports/screener_component_ic.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rule_expectancy as rx  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COST = 0.0061


def load(conn, d_from, d_to):
    sql = """
        SELECT m.stock_code AS code, m.trade_date AS dt, m.close_price AS close,
               m.high_price AS high, m.low_price AS low,
               m.volume, m.trading_value AS value
        FROM market_data m JOIN stocks s ON s.stock_code = m.stock_code
        WHERE m.trade_date BETWEEN %s AND %s AND m.close_price > 0
          AND s.instrument_type = 'STOCK'
    """
    df = pd.read_sql(sql, conn, params=(d_from, d_to))
    df["dt"] = pd.to_datetime(df["dt"])
    return df.sort_values(["code", "dt"]).reset_index(drop=True)


def build(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("code", sort=False)
    df["ret1"] = df["close"] / g["close"].shift(1) - 1
    df["ret3"] = df["close"] / g["close"].shift(3) - 1
    df["ret5"] = df["close"] / g["close"].shift(5) - 1
    df["ret20"] = df["close"] / g["close"].shift(20) - 1
    df["value_ma20"] = g["value"].transform(lambda s: s.shift(1).rolling(20, min_periods=10).mean())
    df["value_ratio"] = df["value"] / df["value_ma20"]
    rng = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["atr_pct"] = rng.groupby(df["code"]).transform(lambda s: s.rolling(14, min_periods=7).mean())
    hl = (df["high"] - df["low"]).replace(0, np.nan)
    df["close_strength"] = (df["close"] - df["low"]) / hl
    df["fwd1"] = g["close"].shift(-1) / df["close"] - 1
    df["fwd3"] = g["close"].shift(-3) / df["close"] - 1
    return df


def excess_by_decile(d: pd.DataFrame, col: str, target: str, quantiles: int = 10):
    """일별 횡단면 초과수익(그날 평균 차감) → 구성요소 분위별 평균·t."""
    d = d[[col, target, "dt"]].dropna()
    if d.empty:
        return {}
    d = d.copy()
    d["ex"] = d[target] - d.groupby("dt")[target].transform("mean")
    d["q"] = d.groupby("dt")[col].transform(
        lambda s: pd.qcut(s.rank(method="first"), quantiles, labels=False, duplicates="drop"))
    out = {}
    for q, g in d.groupby("q"):
        r = g["ex"].to_numpy(dtype=float)
        if r.size < 30:
            continue
        sd = r.std(ddof=1)
        out[int(q)] = {
            "n": int(r.size),
            "excess_pct": round(float(r.mean()) * 100, 4),
            "t": round(float(r.mean() / (sd / np.sqrt(r.size))), 2) if sd > 0 else None,
        }
    # 상위분위 − 하위분위 스프레드
    if 0 in out and (quantiles - 1) in out:
        out["top_minus_bottom_pct"] = round(
            out[quantiles - 1]["excess_pct"] - out[0]["excess_pct"], 4)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2026-01-02")
    ap.add_argument("--to", dest="d_to", default="2026-11-19")
    ap.add_argument("--min-value", type=float, default=3e8)
    ap.add_argument("--min-price", type=float, default=1000.0)
    ap.add_argument("--json-out", default=os.path.join(HERE, "data/reports/screener_component_ic.json"))
    args = ap.parse_args(argv)

    conn = rx._conn()
    df = build(load(conn, args.d_from, args.d_to))
    conn.close()

    uni = df[(df["value"] >= args.min_value) & (df["close"] >= args.min_price)]
    base1 = uni["fwd1"].mean()
    base3 = uni["fwd3"].mean()
    print("유니버스 표본 %d · fwd1 평균 %+.3f%% · fwd3 %+.3f%% (시장 드리프트)" % (
        len(uni), base1 * 100, base3 * 100))

    comps = ["ret1", "ret3", "ret5", "ret20", "value_ratio", "atr_pct", "close_strength"]
    out = {"window": [args.d_from, args.d_to], "universe_n": int(len(uni)),
           "market_fwd1_pct": round(base1 * 100, 4), "market_fwd3_pct": round(base3 * 100, 4),
           "components": {}}

    for col in comps:
        res1 = excess_by_decile(uni, col, "fwd1")
        res3 = excess_by_decile(uni, col, "fwd3")
        out["components"][col] = {"fwd1": res1, "fwd3": res3}
        spread1 = res1.get("top_minus_bottom_pct")
        spread3 = res3.get("top_minus_bottom_pct")
        print("%-14s fwd1 상위−하위 %+7.3f%% | fwd3 %+7.3f%% | 상위분위 fwd1 %+7.3f%%" % (
            col, spread1 or 0.0, spread3 or 0.0,
            (res1.get(9) or {}).get("excess_pct") or 0.0))

    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("[claim] screener_component_ic -> %s components=%d" % (
        os.path.basename(args.json_out), len(comps)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
