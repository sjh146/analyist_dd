#!/usr/bin/env python3
"""단일 피처의 예측력 프로브 — 일별 rank-IC + 5분위 선행수익 (재현용).

WHY(2026-11-19): `short_selling_ratio` 를 패널에서 부활시킨 뒤, **유동성 필터 유무에 따라
IC 부호가 뒤집히는 것**을 발견했다.
  · 전체 시장: IC **-0.023**(중앙 -0.035) — 5분위 q0(공매도 최저) +5.44% 로 이상치 주도
  · 거래대금 3억 이상(스크리너 하드필터와 동일): IC **+0.063**(중앙 +0.066, 음수일 25%)
    → 유동성 종목에서는 **공매도 비중이 높을수록 5일 선행수익이 높다**(모노톤).
즉 "피처가 살아났다"만으로는 부족하고, **어느 유니버스에서 재는지**가 결론을 바꾼다.
이 스크립트는 그 측정을 고정해 재현 가능하게 만든다.

사용:
  /usr/bin/python3 scripts/feature_ic_probe.py --feature short_selling_ratio \
      --from 2026-04-01 --to 2026-11-18 --min-trading-value 300000000
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SQL = {
    # 공매도 비중: short_volume/total_volume, 없으면 short_ratio/100 (D+1 as-of)
    "short_selling_ratio": """
        with px as (
          select stock_code, trade_date, close_price::float8 px,
                 lead(close_price::float8, 5) over (partition by stock_code order by trade_date) fwd5
          from market_data where trade_date between %(d0)s and %(d1)s
        ),
        ss as (
          select stock_code, trade_date,
                 case when coalesce(total_volume,0) > 0 then short_volume::float8 / total_volume
                      else short_ratio::float8 / 100 end r
          from krx_short_selling
        ),
        ss_lag as (
          select stock_code, trade_date, r,
                 lead(trade_date) over (partition by stock_code order by trade_date) next_d
          from ss
        )
        select p.stock_code, p.trade_date, p.px, p.fwd5, s.r as feat,
               m.trading_value::float8 tv
        from px p
        join ss_lag s on s.stock_code = p.stock_code
         and p.trade_date > s.trade_date and (s.next_d is null or p.trade_date <= s.next_d)
        join market_data m on m.stock_code = p.stock_code and m.trade_date = p.trade_date
        where p.fwd5 is not null and p.px > 0 and s.r is not null
    """,
}


def connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def daily_ic(df, feat="feat", ret="ret5", min_names=20):
    out = []
    for _, g in df.groupby("trade_date"):
        if len(g) < min_names or g[feat].nunique() < 3:
            continue
        out.append(g[feat].rank().corr(g[ret].rank()))
    return np.array([x for x in out if x == x])


def describe(vals):
    if len(vals) == 0:
        return {"n": 0, "mean": None, "median": None}
    return {"n": int(len(vals)), "mean": float(np.mean(vals)),
            "median": float(np.median(vals)), "pos_pct": float(100 * np.mean(vals > 0)),
            "neg_pct": float(100 * np.mean(vals < 0))}


def main(argv=None):
    ap = argparse.ArgumentParser(description="단일 피처 IC 프로브")
    ap.add_argument("--feature", default="short_selling_ratio", choices=sorted(SQL))
    ap.add_argument("--from", dest="d_from", default="2026-04-01")
    ap.add_argument("--to", dest="d_to", default="2026-11-18")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--min-trading-value", type=float, default=300_000_000,
                    help="0 이면 필터 없음(전체 시장). 스크리너 하드필터와 같은 기준은 3억")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    df = pd.read_sql(SQL[args.feature], connect(),
                     params={"d0": args.d_from, "d1": args.d_to})
    df = df.rename(columns={"feat": "feat"})
    df["ret5"] = df["fwd5"] / df["px"] - 1

    result = {"feature": args.feature, "window": [args.d_from, args.d_to],
              "horizon": args.horizon,
              "min_trading_value": args.min_trading_value, "splits": {}}

    for label, sub in (("all", df),
                       ("liquid", df[df["tv"] >= args.min_trading_value] if args.min_trading_value else df)):
        if len(sub) == 0:
            result["splits"][label] = {"rows": 0}
            continue
        sub = sub.copy()
        sub["q"] = pd.qcut(sub["feat"].rank(method="first"), 5, labels=False)
        q = sub.groupby("q")["ret5"].agg(["mean", "median", "count"])
        result["splits"][label] = {
            "rows": int(len(sub)), "codes": int(sub["stock_code"].nunique()),
            "ic": describe(daily_ic(sub)),
            "quintiles": {str(int(k)): {"mean": float(r["mean"]), "median": float(r["median"]),
                                        "count": int(r["count"])} for k, r in q.iterrows()},
        }

    out = args.json_out or os.path.join(
        REPO, "data", "reports", "feature_ic_%s.json" % args.feature)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    for label, s in result["splits"].items():
        ic = s.get("ic") or {}
        print(f"{label:7s} rows={s.get('rows'):<7} codes={s.get('codes'):<5} "
              f"IC mean={(ic.get('mean') or 0):+.4f} median={(ic.get('median') or 0):+.4f} "
              f"neg_days={(ic.get('neg_pct') or 0):.0f}%")
    print("저장:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
