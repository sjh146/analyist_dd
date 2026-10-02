"""수리 패널에서 value_per/value_pbr/quality_roe 가 각각 per_current/pbr_current/roe 와
동일 열인지(=수리 부작용 aliasing) 확인. 읽기 전용.
"""
import numpy as np

for p in ["/app/app/models/wf/panel_420_asof2.npz", "/app/app/models/wf/panel_420_asofpatch.npz"]:
    z = np.load(p, allow_pickle=True)
    X = z["X"].astype(float)
    feats = [str(f) for f in z["feature_names"]]
    idx = {f: i for i, f in enumerate(feats)}
    print("=" * 60)
    print(p)
    pairs = [("value_per", "per_current"), ("value_pbr", "pbr_current"),
             ("quality_roe", "roe"), ("value_ncav", "net_income")]
    for a, b in pairs:
        if a in idx and b in idx:
            x, y = X[:, idx[a]], X[:, idx[b]]
            same = bool(np.array_equal(x, y))
            nz = (x != 0) & (y != 0)
            corr = float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else float("nan")
            print(f"  {a:14s} vs {b:14s} identical={same} corr={corr:+.4f} both_nz={int(nz.sum())} "
                  f"nz_a={float((x!=0).mean()):.3f} nz_b={float((y!=0).mean()):.3f}")
