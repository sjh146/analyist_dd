"""CORE_FEATURES(48) ∩ 종목상수 피처 — 누수게이트③이 배포 경로에서 성립하는가."""
import sys

import numpy as np

sys.path.insert(0, "/app")
from app.models.train_curated import CORE_FEATURES  # noqa: E402

z = np.load("/app/app/models/wf/panel_200liq_fresh.npz", allow_pickle=True)
X = z["X"]
names = [str(s) for s in z["feature_names"]]
codes = np.asarray(z["codes"], dtype=object)

const = []
for j, name in enumerate(names):
    col = X[:, j]
    ok = 0
    tot = 0
    for c in np.unique(codes):
        v = col[codes == c]
        v = v[~np.isnan(v)]
        tot += 1
        if v.size and np.unique(v).size == 1:
            ok += 1
    if ok / max(tot, 1) >= 0.99:
        const.append(name)

core = list(CORE_FEATURES)
print("core n", len(core))
print("core∩panel", len(set(core) & set(names)))
print("stock_const n", len(const))
print("stock_const ∩ core:", sorted(set(const) & set(core)))
print("core 중 패널에 없음:", sorted(set(core) - set(names)))
# core 중 표준편차 0 인 컬럼
zero_core = [n for n in core if n in names and float(np.nanstd(X[:, names.index(n)])) == 0.0]
print("core 중 std=0:", zero_core)
