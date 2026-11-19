"""CG367 — momentum_3_12m 커버리지 0.21 이 '측정 불가'인지 '무정보'인지 분해.

왜(실측 2026-11-20 00:07 CG364): 패널 스크린에서 세 부활 피처는
  momentum_3_12m AUC 0.4893 (|Δ|0.0107, **커버리지 0.2115**, IC t −0.85 / 62날짜)
  relative_strength 0.4986 (|Δ|0.0014, cov 1.000)
  bb_position      0.5003 (|Δ|0.0003, cov 1.000)
로 전부 선별 문턱(0.044) 미달이었다. 그러나 momentum 만 커버리지가 21%라
'값이 없는 79% 가 AUC 를 0.5 로 끌어내린 것'과 '값이 있는 구간에도 엣지가 없는 것'
을 구분할 수 없다 — 축 종결(CG364)의 유일한 미측정 칸이다.

방법: 결측 아닌 행만 남긴 서브셋 AUC + 값이 존재하는 날짜만의 횡단면 IC(t) +
날짜별 커버리지 추이. 대조로 커버리지 1.0 인 두 피처도 같은 서브셋 규칙으로 재계산해
스크립트가 CG364 값을 재현하는지(자기검증) 확인한다.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_feature_screen import auc_rank, fwd_label  # noqa: E402

PANEL = "/app/app/models/wf/panel_420_asofpatch.npz"
COLS = ["momentum_3_12m", "relative_strength", "bb_position", "market_impact_score"]


def main():
    panel = sys.argv[1] if len(sys.argv) > 1 else PANEL
    z = np.load(panel, allow_pickle=True)
    dates = pd.to_datetime(z["dates"])
    codes = [str(c) for c in z["codes"]]
    lab, ret = fwd_label(dates, codes, z["price"], kind="abs")
    key = pd.DataFrame({"code": codes, "date": dates})

    import psycopg2
    conn = psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"),
                            port=os.environ.get("POSTGRES_PORT", "5432"),
                            user=os.environ["POSTGRES_USER"],
                            password=os.environ["POSTGRES_PASSWORD"],
                            dbname=os.environ["POSTGRES_DB"])
    cur = conn.cursor()
    cur.execute(
        f"select stock_code, trade_date, {', '.join(COLS)} from supply_market_features "
        "where stock_code = any(%s) and trade_date between %s and %s",
        (list(set(codes)), dates.min().date(), dates.max().date()))
    f = pd.DataFrame(cur.fetchall(), columns=["code", "date"] + COLS)
    f["date"] = pd.to_datetime(f["date"])
    cur.close()
    conn.close()

    m = key.merge(f, on=["code", "date"], how="left")
    m["fwd"] = ret
    m["lab"] = lab

    out = {"panel": os.path.basename(panel), "rows": int(len(m)),
           "from": str(dates.min().date()), "to": str(dates.max().date()),
           "label": "abs h5 (ret>0)", "features": {}}

    for c in COLS:
        x = pd.to_numeric(m[c], errors="coerce").to_numpy(dtype=float)
        present = ~np.isnan(x)
        nz = present & (x != 0)
        full_auc = auc_rank(x, lab)
        sub_auc = auc_rank(np.where(nz, x, np.nan), lab)
        # 값이 있는 날짜만의 IC
        ics, ic_dates = [], 0
        tmp = pd.DataFrame({"date": m["date"], "x": x, "fwd": ret}).dropna()
        for _, g in tmp.groupby("date"):
            if len(g) >= 10 and g["x"].nunique() > 1:
                ics.append(float(g["x"].corr(g["fwd"], method="spearman")))
                ic_dates += 1
        ic_mean = round(float(np.mean(ics)), 4) if ics else None
        ic_t = (round(float(np.mean(ics) / (np.std(ics, ddof=1) / np.sqrt(len(ics)))), 2)
                if len(ics) > 3 and np.std(ics, ddof=1) > 0 else None)
        # 날짜별 커버리지(비영) 추이 — 앞/뒤 절반
        byday = pd.DataFrame({"date": m["date"], "nz": nz}).groupby("date")["nz"].mean()
        byday = byday.sort_index()
        half = len(byday) // 2
        out["features"][c] = {
            "coverage_notna": round(float(present.mean()), 4),
            "coverage_nonzero": round(float(nz.mean()), 4),
            "auc_full": None if full_auc is None else round(full_auc, 4),
            "auc_nonzero_subset": None if sub_auc is None else round(sub_auc, 4),
            "n_rows_nonzero": int(nz.sum()),
            "xs_ic_mean": ic_mean, "xs_ic_t": ic_t, "n_ic_dates": ic_dates,
            "nz_rate_first_half": round(float(byday.iloc[:half].mean()), 4),
            "nz_rate_second_half": round(float(byday.iloc[half:].mean()), 4),
            "n_dates_any_nonzero": int((byday > 0).sum()),
        }

    print(json.dumps(out, ensure_ascii=False, indent=1))
    dst = os.environ.get("OUT", "/app/reports/overnight/cg367_momentum_subset.json")
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("saved:", dst)


if __name__ == "__main__":
    main()
