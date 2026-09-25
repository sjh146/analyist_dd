#!/usr/bin/env python3
"""_ev_feature_screen.py — 부활 피처(ev 패널) 누수·품질 게이트 스크린.

목적(엔지니어 판정규칙 3): 새로 들어온 피처가 선별을 지배하거나 누수인지 먼저 본다.
- 단일 피처 AUC > 0.75 → 누수 후보(중단)
- 종목별 유니크값 1개(종목 상수) 비율 → 종목 식별 증폭 위험
- 커버리지(비결측 비율) → dead 후보
라벨: 코드별 forward 5일 수익률 부호(패널 price·dates·codes 로 직접 계산, 스윕과 동일 계열).
출력: JSON 한 덩어리(stdout). 판정은 사람이 아니라 판정규칙이 한다.
"""
import json
import sys

import numpy as np
import pandas as pd

BASE = "/app/app/models/wf/panel_420_asofpatch.npz"
EV = "/app/app/models/wf/panel_420_asofpatch_ev.npz"


def load(p):
    z = np.load(p, allow_pickle=True)
    return z["X"], [str(n) for n in z["feature_names"]], z["dates"], z["codes"], z["price"]


def fwd_label(dates, codes, price, horizon=5):
    df = pd.DataFrame({"date": pd.to_datetime(dates), "code": codes, "px": price})
    df = df.sort_values(["code", "date"])
    df["fwd"] = df.groupby("code")["px"].shift(-horizon) / df["px"] - 1.0
    return df["fwd"].to_numpy(), df["date"].to_numpy()


def auc_rank(x, y):
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 100 or len(np.unique(y[m])) < 2:
        return None
    r = pd.Series(x[m]).rank().to_numpy()
    pos = y[m] == 1
    npos, nneg = int(pos.sum()), int((~pos).sum())
    if npos == 0 or nneg == 0:
        return None
    return float((r[pos].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def main():
    Xb, nb, db, cb, pb = load(BASE)
    Xe, ne, de, ce, pe = load(EV)
    fwd, fdates = fwd_label(de, ce, pe)
    lab = np.where(np.isnan(fwd), np.nan, (fwd > 0).astype(float))

    rows = {}
    # ev 패널에서만 있는 '컬럼 인덱스'를 이름 중복 때문에 인덱스 기준으로 찾는다.
    # 두 패널은 같은 행 순서(같은 date/code 순열)이므로 행 기준 비교가 성립한다.
    same_rows = (len(db) == len(de) and np.array_equal(np.asarray(db), np.asarray(de))
                 and np.array_equal(np.asarray(cb), np.asarray(ce)))
    new_idx = []
    b_cols = list(nb)
    for j, name in enumerate(ne):
        # 이름이 같은 base 컬럼이 아직 소진되지 않았으면 같은 피처로 본다(중복명 대응).
        if name in b_cols:
            b_cols.remove(name)
        else:
            new_idx.append(j)

    for j in new_idx:
        x = Xe[:, j].astype(float)
        cov = float(np.mean(~np.isnan(x)))
        df = pd.DataFrame({"code": ce, "x": x})
        uniq = df.groupby("code")["x"].nunique(dropna=True)
        const_ratio = float(np.mean(uniq.to_numpy() <= 1))
        rows[ne[j]] = {
            "col": j,
            "coverage": round(cov, 4),
            "stock_const_ratio": round(const_ratio, 4),
            "uniq_median": float(np.median(uniq.to_numpy())),
            "single_feature_auc": (None if auc_rank(x, lab) is None else round(auc_rank(x, lab), 4)),
        }

    leak = {k: v for k, v in rows.items() if (v["single_feature_auc"] or 0) > 0.75}
    dead = {k: v for k, v in rows.items() if v["coverage"] < 0.05}
    const = {k: v for k, v in rows.items() if v["stock_const_ratio"] > 0.5}
    print(json.dumps({
        "base_shape": list(Xb.shape), "ev_shape": list(Xe.shape),
        "rows_aligned": bool(same_rows),
        "n_new": len(new_idx), "new_features": rows,
        "gate": {
            "leak_auc_gt_075": sorted(leak),
            "dead_coverage_lt_005": sorted(dead),
            "stock_constant_gt_050": sorted(const),
        },
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
