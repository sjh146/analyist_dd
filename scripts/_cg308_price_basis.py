#!/usr/bin/env python3
"""_cg308_price_basis.py — 패널 price vs market_data.close_price 기준 진단(임시).

목적: 라벨 fwd_ret(wf_label_sweep.py:2096 — 패널 `price` shift(-h)/price−1)과
      돈 지표 gross_pct(fillable_topk_expectancy.py:139-145 — market_data.close_price)가
      corr 0.53 으로 어긋나는 원인을 특정한다(수정주가 여부·분할 흔적).
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app/scripts")
import fillable_topk_expectancy as F  # noqa: E402

PANEL = "/app/app/models/wf/panel_prod200.npz"


def main():
    z = np.load(PANEL, allow_pickle=True)
    dates = np.asarray(z["dates"]).astype(str)
    codes = np.asarray(z["codes"]).astype(str)
    price = np.asarray(z["price"], dtype=float)
    df = pd.DataFrame({"date": dates, "code": codes, "panel_price": price})
    df = df[df["panel_price"] > 0]
    print("panel rows", len(df), "dates", df["date"].nunique(), "codes", df["code"].nunique(),
          "range", df["date"].min(), "~", df["date"].max())

    dmin = df["date"].min()
    dmax = df["date"].max()
    conn = F.db_connect()
    mk = pd.read_sql(
        "SELECT stock_code, trade_date, close_price FROM market_data "
        "WHERE stock_code = ANY(%s::text[]) AND trade_date BETWEEN %s AND %s",
        conn, params=[sorted(df["code"].unique().tolist()), dmin, dmax])
    mk["code"] = mk["stock_code"].astype(str).str.zfill(6)
    mk["date"] = pd.to_datetime(mk["trade_date"]).dt.strftime("%Y-%m-%d")
    mk = mk[["code", "date", "close_price"]]
    m = df.merge(mk, on=["code", "date"], how="inner")
    print("joined rows", len(m), "of panel", len(df), "(db missing %.1f%%)" % (100 * (1 - len(m) / len(df))))

    m["ratio"] = m["panel_price"] / m["close_price"]
    print("ratio panel/close: median %.4f  p1 %.4f  p99 %.4f  within 1%% %.1f%%"
          % (m["ratio"].median(), m["ratio"].quantile(0.01), m["ratio"].quantile(0.99),
             100 * ((m["ratio"] - 1).abs() < 0.01).mean()))

    # 종목 내 비율 단절 = 수정주가 vs 원주가(분할·증자) 흔적
    m = m.sort_values(["code", "date"])
    m["ratio_chg"] = m.groupby("code")["ratio"].pct_change()
    brk = m[m["ratio_chg"].abs() > 0.10]
    print("종목 내 비율 단절(|Δratio|>10%%) 행 %d · 종목 %d" % (len(brk), brk["code"].nunique()))
    if len(brk):
        print(brk.head(5)[["code", "date", "panel_price", "close_price", "ratio", "ratio_chg"]].to_string(index=False))

    # 라벨 정의(패널 price shift -5)와 돈 지표 정의(시장 종가 i+5)로 같은 행 5일 수익 비교
    g = m.pivot_table(index="date", columns="code", values="panel_price")
    c = m.pivot_table(index="date", columns="code", values="close_price")
    lab = g.shift(-5) / g - 1.0
    gross = (c.shift(-5) / c - 1.0) * 100.0
    a = lab.stack().rename("label_frac")
    b = gross.stack().rename("gross_pct")
    j = pd.concat([a, b], axis=1).dropna()
    print("n %d  corr %.4f  label mean %.4f(frac)  gross mean %.3f(%%p)" % (len(j), j.corr().iloc[0, 1], j["label_frac"].mean(), j["gross_pct"].mean()))
    d = j["gross_pct"] - 100 * j["label_frac"]
    print("gross − 100*label : mean %+.3f sd %.3f  |>1| %.1f%%  |>5| %.1f%%"
          % (d.mean(), d.std(), 100 * (d.abs() > 1).mean(), 100 * (d.abs() > 5).mean()))
    worst = d.abs().sort_values(ascending=False).head(5)
    for (dt, cd), _ in worst.items():
        print("  %s %s label %.4f gross %+.2f" % (dt, cd, j.loc[(dt, cd), "label_frac"], j.loc[(dt, cd), "gross_pct"]))


if __name__ == "__main__":
    main()
