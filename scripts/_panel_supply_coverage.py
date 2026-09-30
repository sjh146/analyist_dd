#!/usr/bin/env python3
"""패널의 수급·공매도 계열 피처 실측 — 비영 비율 + 단일피처 AUC(전체/시간가변).

왜: L3(데이터 축)의 유일한 미측정 지점은 '이미 있는 테이블이 패널에서 실제로 값을 갖는가'다.
foreign_institutional 은 1,040종목·142,996행·비영 71~94% 로 살아 있는데, 그 컬럼에서 만든
피처가 패널에서 0 이면 구현 배관 문제(데이터 부재가 아니다)다. 판정 지표: 비영 비율 + |AUC−0.5|.

실행: docker exec stock_xgboost_ml python /app/scripts/_panel_supply_coverage.py
"""
import os
import re
import sys

import numpy as np

PANELS = [
    ("/app/app/models/wf/panel_420_asofpatch.npz", "210피처·49종목(281일)"),
    ("/app/app/models/wf/panel_995.npz", "213피처·49종목(659일)"),
]
PAT = re.compile(r"foreign|institution|net_buy|short|supply|ownership|days_to_cover|"
                 r"momentum_3_12|program|individual", re.I)


def auc(y, x):
    """단순 rank AUC (동점은 평균순위)."""
    m = np.isfinite(x)
    y, x = y[m], x[m]
    if len(y) < 50 or len(set(y.tolist())) < 2:
        return None
    r = np.argsort(np.argsort(x)).astype(np.float64) + 1.0
    # 동점 평균순위
    _, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt))
    np.add.at(sums, inv, r)
    r = (sums / cnt)[inv]
    n1 = float((y == 1).sum())
    n0 = float((y == 0).sum())
    if n1 == 0 or n0 == 0:
        return None
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


for path, label in PANELS:
    if not os.path.exists(path):
        print(f"[skip] {path} 없음")
        continue
    d = np.load(path, allow_pickle=True)
    keys = list(d.keys())
    names = [str(x) for x in d["feature_names"]] if "feature_names" in keys else []
    X = d["X"] if "X" in keys else None
    y = d["y"] if "y" in keys else None
    print(f"\n=== {os.path.basename(path)} ({label}) keys={keys} "
          f"X={None if X is None else X.shape} n_features={len(names)}")
    if X is None:
        continue
    if y is None and "price" in keys and "codes" in keys and "dates" in keys:
        # 라벨 없음 → LS_quant_q30_h5 정의로 즉석 생성(종목별 5일 선행수익 → 날짜별 중앙값 분할)
        import pandas as pd
        df = pd.DataFrame({"code": [str(c) for c in d["codes"]],
                           "date": [str(x) for x in d["dates"]],
                           "price": np.asarray(d["price"], dtype=float)})
        fwd = np.full(len(df), np.nan)
        for _c, g in df.groupby("code", sort=False):
            p = g["price"].to_numpy()
            v = np.full(len(p), np.nan)
            for j in range(len(p) - 5):
                v[j] = p[j + 5] / p[j] - 1.0
            fwd[g.index.to_numpy()] = v
        med = df.assign(f=fwd).groupby("date")["f"].transform("median").to_numpy()
        ok = np.isfinite(fwd) & np.isfinite(med)
        y = np.where(ok, (fwd > med).astype(int), -1)
        print(f"  라벨 즉석 생성(h5 분위0.30 아님 → 중앙값 분할): 유효 {int(ok.sum())}/{len(y)}"
              f" · 양성률 {float((y[ok] == 1).mean()):.3f}")
    y = np.asarray(y).astype(int).ravel()
    keep = y >= 0
    X = X[keep]
    y = y[keep]
    hits = [(i, n) for i, n in enumerate(names) if PAT.search(n)]
    print(f"수급·공매도 관련 피처 {len(hits)}개")
    rows = []
    for i, n in hits:
        col = X[:, i].astype(np.float64)
        nz = 100.0 * float((col != 0).sum()) / len(col)
        a = auc(y, col)
        rows.append((n, nz, a))
    rows.sort(key=lambda t: -(abs((t[2] or 0.5) - 0.5)))
    for n, nz, a in rows:
        print(f"  {n:38s} nonzero {nz:6.2f}%  AUC {'n/a' if a is None else f'{a:.4f}'}")
    live = [r for r in rows if r[1] >= 50.0]
    print(f"  → 비영 ≥50% 피처: {len(live)}개 / {len(rows)}개")
