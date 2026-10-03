#!/usr/bin/env python3
"""청정 패널(prod200) SNS/attention 계열의 **결측(NaN)** 시간분포 — 강제투입 실험 가능성 판정.

주의: SNS 컬럼의 결측은 NaN 으로 들어 있다(0.0 아님). `col != 0` 으로 세면 NaN 이 True 로
잡혀 'fill 100%' 로 오독한다(2026-10-04 00:2x 실측 실수). 반드시 `.notna()` 로 센다.
"""
import os

import numpy as np
import pandas as pd

P = "/app/app/models/wf/panel_prod200.npz"
if not os.path.exists(P):
    P = "/home/jhshi/analyist_dd/services/xgboost-ml/app/models/wf/panel_prod200.npz"
z = np.load(P, allow_pickle=True)
names = [str(x) for x in z["feature_names"]]
X = z["X"].astype(float)
dates = pd.to_datetime(z["dates"])
df = pd.DataFrame(X, columns=names)
df["date"] = dates

want = [n for n in names if n.startswith("sns_") or n.startswith("kalman_")]
print(f"SNS/kalman 컬럼 {len(want)}개 · 패널 {len(df)}행")
print("\n[비영(NaN 아님) 비율 · 기간]")
for n in want:
    s = df[n]
    nn = s.notna()
    if nn.sum() == 0:
        print(f"  {n:38s} 전부 NaN")
        continue
    sub = df.loc[nn, "date"]
    print(f"  {n:38s} fill={nn.mean():.4f} n={nn.sum():6d} 기간 {sub.min().date()}~{sub.max().date()}")

n = "sns_attention_score"
if n in names:
    m = df.groupby(df["date"].dt.to_period("M"))[n].apply(lambda s: float(s.notna().mean()))
    print(f"\n[{n}] 월별 비결측 비율:")
    print("  " + "  ".join(f"{k}:{v:.3f}" for k, v in m.items()))
    nz = df.loc[df[n].notna(), n]
    print(f"  비결측 값 분포: min={nz.min():.4f} median={nz.median():.4f} max={nz.max():.4f} "
          f"nonzero={float((nz != 0).mean()):.4f}")
