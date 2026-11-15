#!/usr/bin/env python3
"""_cg308_exit_align.py — 돈 지표의 청산 인덱스 정합성(임시).

fillable_topk_expectancy.enrich 는 DB 시계열에서 'i+horizon 번째 행'을 청산으로 쓴다.
패널 기반 라벨(shift(-5))은 패널 행 기준 5번째 행이다. 두 인덱스 규칙이 다른 날짜를
가리키는지(결측·정지일 때문에) 확인하고, 날짜가 같을 때 gross 가 fwd_ret*100 과 일치하는지 본다.
"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app/scripts")
import fillable_topk_expectancy as F  # noqa: E402

PANEL = "/app/app/models/wf/panel_prod200.npz"
DUMP = "/app/reports/overnight/cg291_preds.jsonl"


def main():
    z = np.load(PANEL, allow_pickle=True)
    df = pd.DataFrame({"date": np.asarray(z["dates"]).astype(str),
                       "code": np.asarray(z["codes"]).astype(str),
                       "price": np.asarray(z["price"], dtype=float)})
    df = df[df["price"] > 0].sort_values(["code", "date"])
    df["label"] = df.groupby("code", sort=False)["price"].shift(-5) / df["price"] - 1.0

    rows = [json.loads(l) for l in open(DUMP)]
    rows = [r for r in rows if r["exp"] == "CO_q05_h5"]
    codes = sorted({r["code"] for r in rows})
    dmin, dmax = min(r["date"] for r in rows), max(r["date"] for r in rows)
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=42)).date()
    conn = F.db_connect()
    series = F.price_series(conn, codes, d0, d1)

    # 종목별 패널 날짜순 대비 DB 날짜 수
    extra = []
    for code in codes[:200]:
        s = series.get(code)
        if not s:
            continue
        pset = set(df[df["code"] == code]["date"])
        dbset = {str(x) for x in s["d"]}
        e = len(dbset - pset)
        if e:
            extra.append((code, e, len(dbset), len(pset)))
    print("codes with DB-only dates: %d / %d · 예시 %s" % (len(extra), len(codes), extra[:3]))

    # 패널 날짜 → 인덱스 사전(코드별) — 루프 안에서 DataFrame 필터링은 O(n^2) 라 금지
    pidx_map = {}
    for code, g in df.groupby("code", sort=True):
        ds = list(g["date"])
        pidx_map[code] = ({d: i for i, d in enumerate(ds)}, ds)

    bad, ok, nodate = 0, 0, 0
    exg, exf = [], []
    for r in rows:
        s = series.get(r["code"])
        if not s:
            continue
        try:
            i = s["d"].index(pd.Timestamp(r["date"]).date())
        except ValueError:
            continue
        if i + 5 >= len(s["d"]):
            continue
        exit_date_db = str(s["d"][i + 5])
        # 패널 기준 5행 뒤 날짜
        pos, ds = pidx_map.get(r["code"], ({}, []))
        j = pos.get(r["date"])
        if j is None:
            nodate += 1
            continue
        exit_date_panel = ds[j + 5] if j + 5 < len(ds) else None
        if exit_date_panel == exit_date_db:
            ok += 1
            exg.append(((s["c"][i + 5] / s["c"][i] - 1.0) * 100.0))
            exf.append(r["fwd_ret"] * 100.0)
        else:
            bad += 1
    print("exit date same %d · different %d · panel date missing %d" % (ok, bad, nodate))
    if exg:
        a = np.array(exg)
        b = np.array(exf)
        print("same-date rows: n %d corr %.6f · max |gross-fwd*100| %.6f" % (len(a), np.corrcoef(a, b)[0, 1], np.abs(a - b).max()))
        print("  mean gross %+.4f · mean fwd*100 %+.4f" % (a.mean(), b.mean()))


if __name__ == "__main__":
    main()
