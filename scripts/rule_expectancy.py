#!/usr/bin/env python3
"""룰 단독 기대값 측정 — 모델·AUC를 우회해 "이 신호 자체가 돈인가"를 직접 잰다.

WHY(2026-11-20): 네 번의 모델 실험(피처 부활·선별 폭·희박 이벤트·지평)이 모두 기대값 (+)
를 못 냈다. AUC 는 올라도 돈이 안 됐다(h1 AUC 0.5568 → 기대값 -0.145%/건). 그래서 신호를
**모델에 넣지 않고** 단독 룰로 매매했을 때의 기대값을 직접 잰다.

측정 규칙(모두 종가 기준, 진입 = 신호일 종가, 청산 = 다음 거래일 종가):
  R1 short_high   : 공매도 비중 상위 10%
  R2 short_low    : 공매도 비중 하위 10%
  R3 up_3_8       : 당일 등락 +3% ~ +8%
  R4 up_10plus    : 당일 등락 +10% 이상
  R5 value_surge  : 거래대금 5일 평균 대비 3배 이상 + 당일 상승
  R6 news_top     : 뉴스 언급(7일) 상위 10%

비용: 왕복 --cost-bps(기본 21) + 편도 슬리피지 --slip-bps(기본 20) × 2.
유니버스: 거래대금 --min-value(기본 3억) 이상, 가격 --min-price(기본 1,000원) 이상.

사용:
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 python3 scripts/rule_expectancy.py \
      --from 2026-01-02 --to 2026-11-19 --json-out data/reports/rule_expectancy.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
    )


def load_prices(conn, d_from, d_to):
    sql = """
        SELECT stock_code AS code, trade_date AS dt, close_price AS close,
               volume, trading_value AS value
        FROM market_data
        WHERE trade_date BETWEEN %s AND %s AND close_price > 0
    """
    df = pd.read_sql(sql, conn, params=(d_from, d_to))
    df["dt"] = pd.to_datetime(df["dt"])
    df = df.sort_values(["code", "dt"]).reset_index(drop=True)
    return df


def add_forward(df: pd.DataFrame) -> pd.DataFrame:
    """다음 거래일 종가 수익률(fwd1)과 당일 등락(r_day)을 붙인다."""
    g = df.groupby("code", sort=False)
    df["fwd1"] = g["close"].shift(-1) / df["close"] - 1.0
    df["r_day"] = df["close"] / g["close"].shift(1) - 1.0
    df["value_ma5"] = g["value"].transform(lambda s: s.shift(1).rolling(5, min_periods=3).mean())
    return df


def load_short(conn, d_from, d_to):
    # 실제 스키마(실측 2026-11-20): short_ratio 가 이미 비중 컬럼으로 존재하고
    # 거래량 컬럼명은 total_volume 이다(volume 없음 → n=0 버그의 원인).
    sql = """
        SELECT stock_code AS code, trade_date AS dt,
               COALESCE(short_ratio,
                        short_volume::float / NULLIF(total_volume, 0)) AS short_ratio
        FROM krx_short_selling
        WHERE trade_date BETWEEN %s AND %s AND total_volume > 0
    """
    try:
        d = pd.read_sql(sql, conn, params=(d_from, d_to))
    except Exception:
        return pd.DataFrame(columns=["code", "dt", "short_ratio"])
    d["dt"] = pd.to_datetime(d["dt"])
    return d


def load_index(conn, d_from, d_to, index_code="KOSDAQ"):
    """지수 일별 수익률 → 레짐 분류용. (index_daily 는 2026-11-20 백필)"""
    sql = """
        SELECT trade_date AS dt, close
        FROM index_daily WHERE index_code = %s AND trade_date BETWEEN %s AND %s
        ORDER BY trade_date
    """
    try:
        d = pd.read_sql(sql, conn, params=(index_code, d_from, d_to))
    except Exception:
        return pd.DataFrame(columns=["dt", "idx_ret"])
    if d.empty:
        return pd.DataFrame(columns=["dt", "idx_ret"])
    d["dt"] = pd.to_datetime(d["dt"])
    d["idx_ret"] = d["close"].astype(float).pct_change()
    return d[["dt", "idx_ret"]]


def load_news(conn, d_from, d_to):
    sql = """
        SELECT stock_code AS code, trade_date AS dt, mention_7d
        FROM news_mention_features
        WHERE trade_date BETWEEN %s AND %s
    """
    try:
        d = pd.read_sql(sql, conn, params=(d_from, d_to))
    except Exception:
        return pd.DataFrame(columns=["code", "dt", "mention_7d"])
    d["dt"] = pd.to_datetime(d["dt"])
    return d


def _stats(sub: pd.DataFrame, cost: float) -> dict:
    r = (sub["fwd1"] - cost).to_numpy(dtype=float)
    if r.size == 0:
        return {"n": 0, "mean_net_pct": None, "win_rate": None, "t": None}
    mean = float(np.mean(r))
    sd = float(np.std(r, ddof=1)) if r.size > 1 else 0.0
    t = float(mean / (sd / np.sqrt(r.size))) if sd > 0 and r.size > 1 else None
    return {
        "n": int(r.size),
        "mean_net_pct": round(mean * 100, 4),
        "win_rate": round(float(np.mean(r > 0)) * 100, 2),
        "std_pct": round(sd * 100, 3),
        "t": round(t, 3) if t is not None else None,
        "median_net_pct": round(float(np.median(r)) * 100, 4),
    }


def build_rules(df: pd.DataFrame, short: pd.DataFrame, news: pd.DataFrame,
                min_value: float, min_price: float) -> dict:
    base = df[(df["value"] >= min_value) & (df["close"] >= min_price) & df["fwd1"].notna()].copy()
    rules = {}

    m = base.merge(short, on=["code", "dt"], how="left")
    hi = m.groupby("dt")["short_ratio"].transform(lambda s: s.quantile(0.90))
    lo = m.groupby("dt")["short_ratio"].transform(lambda s: s.quantile(0.10))
    rules["R1_short_high"] = m[(m["short_ratio"].notna()) & (m["short_ratio"] >= hi)]
    rules["R2_short_low"] = m[(m["short_ratio"].notna()) & (m["short_ratio"] <= lo)]

    rules["R3_up_3_8"] = base[(base["r_day"] >= 0.03) & (base["r_day"] < 0.08)]
    rules["R4_up_10plus"] = base[base["r_day"] >= 0.10]
    surge = (base["value_ma5"].notna()) & (base["value"] >= 3 * base["value_ma5"]) & (base["r_day"] > 0)
    rules["R5_value_surge"] = base[surge]
    # 반전(역추세) 가설: 우리 시스템은 '상승 종목 매수'인데 위 R3/R4 가 크게 음(-)이면
    # 반대(하락 후 매수)가 (+)인지 같은 표본에서 확인해야 한다(대칭 검정).
    rules["R7_down_3_8"] = base[(base["r_day"] <= -0.03) & (base["r_day"] > -0.08)]
    rules["R8_down_8plus"] = base[base["r_day"] <= -0.08]

    mn = base.merge(news, on=["code", "dt"], how="left")
    nh = mn.groupby("dt")["mention_7d"].transform(lambda s: s.quantile(0.90))
    rules["R6_news_top"] = mn[(mn["mention_7d"].notna()) & (mn["mention_7d"] > 0) & (mn["mention_7d"] >= nh)]
    return rules


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2026-01-02")
    ap.add_argument("--to", dest="d_to", default="2026-11-19")
    ap.add_argument("--cost-bps", type=float, default=21.0)
    ap.add_argument("--slip-bps", type=float, default=20.0)
    ap.add_argument("--min-value", type=float, default=3e8)
    ap.add_argument("--min-price", type=float, default=1000.0)
    ap.add_argument("--index-code", default="KOSDAQ", help="레짐 분류용 지수(기본 KOSDAQ)")
    ap.add_argument("--json-out", default=os.path.join(HERE, "data/reports/rule_expectancy.json"))
    args = ap.parse_args(argv)

    cost = (args.cost_bps + 2 * args.slip_bps) / 10000.0
    conn = _conn()
    df = add_forward(load_prices(conn, args.d_from, args.d_to))
    short = load_short(conn, args.d_from, args.d_to)
    news = load_news(conn, args.d_from, args.d_to)
    idx = load_index(conn, args.d_from, args.d_to, args.index_code)
    conn.close()

    rules = build_rules(df, short, news, args.min_value, args.min_price)
    # 기준선(전 유니버스) — 규칙 값이 시장 전체 드리프트인지 신호인지 가르는 필수 대조군.
    # (실측 2026-11-20: 모든 규칙이 -0.65~-1.0% 로 비슷하게 나와 대조군 없이는 해석 불가)
    for _k in list(rules):
        rules[_k] = rules[_k].merge(idx, on="dt", how="left")
    base_all = df[(df["value"] >= args.min_value) & (df["close"] >= args.min_price)
                  & df["fwd1"].notna()].merge(idx, on="dt", how="left")
    out = {"window": [args.d_from, args.d_to], "cost_pct": round(cost * 100, 3),
           "universe_min_value": args.min_value, "rules": {}}
    base_stats = _stats(base_all, cost)
    if "idx_ret" in base_all.columns:
        base_stats["panic"] = _stats(base_all[base_all["idx_ret"] <= -0.015], cost)
        base_stats["normal"] = _stats(base_all[base_all["idx_ret"] > -0.015], cost)
    base_mean = base_stats["mean_net_pct"] or 0.0
    out["baseline_all"] = base_stats
    out["regime_index"] = args.index_code
    print("기준선(전 유니버스) n=%d mean_net=%+.4f%% (초과수익의 기준)" % (
        base_stats["n"], base_mean))
    print("%-14s %7s %10s %9s %8s %7s" % ("rule", "n", "mean_net%", "excess%", "win%", "t"))
    for name, sub in rules.items():
        s = _stats(sub, cost)
        if "idx_ret" in sub.columns:
            panic = sub[sub["idx_ret"] <= -0.015]
            normal = sub[sub["idx_ret"] > -0.015]
            s["panic"] = _stats(panic, cost)
            s["normal"] = _stats(normal, cost)
        out["rules"][name] = s
        if s["n"]:
            s["excess_pct"] = round(s["mean_net_pct"] - base_mean, 4)
            print("%-14s %7d %+10.4f %+9.4f %8.2f %7s" % (
                name, s["n"], s["mean_net_pct"], s["excess_pct"], s["win_rate"], s["t"]))
        else:
            print("%-14s %7d  (표본 없음)" % (name, 0))
        if s.get("panic") and s["panic"]["n"]:
            print("%-14s   └ 패닉일 n=%d mean=%+.3f%% (초과 %+.3f%%) | 정상일 n=%d mean=%+.3f%%" % (
                "", s["panic"]["n"], s["panic"]["mean_net_pct"],
                s["panic"]["mean_net_pct"] - (base_stats.get("panic", {}).get("mean_net_pct") or 0),
                (s.get("normal") or {}).get("n", 0), (s.get("normal") or {}).get("mean_net_pct") or 0.0))

    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("[claim] rule_expectancy -> %s rules=%d n_total=%d" % (
        os.path.basename(args.json_out), len(out["rules"]),
        sum(v["n"] for v in out["rules"].values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
