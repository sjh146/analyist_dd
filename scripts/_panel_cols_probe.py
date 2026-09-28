#!/usr/bin/env python3
"""패널 컬럼 진단 — 전부 0 인 컬럼(죽은 피처)과 거시/시장레벨 컬럼 확인 (읽기 전용)."""
import sys
import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "/app/app/models/wf/panel_420_asofpatch.npz"
z = np.load(path, allow_pickle=True)
names = [str(n) for n in z["feature_names"]]
X = z["X"]
print("panel", path, "shape", X.shape, "names", len(names))
zero = [n for i, n in enumerate(names) if float(np.nanmax(np.abs(X[:, i]))) == 0.0]
print("all-zero cols:", len(zero))
print("all-zero list:", zero)
pref = ("fx", "oil", "interest", "cpi", "ppi", "yield", "macro", "economic", "cycle", "krx_", "market_")
print("macro/market-ish:", [n for n in names if n.startswith(pref)])
