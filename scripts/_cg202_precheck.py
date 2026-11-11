#!/usr/bin/env python3
"""CG202 arm 사전검증 — 패널 피처명과 exclude_names/derived 소스가 실제로 존재하는지 (학습 없음)."""
import json
import sys

import numpy as np

PANEL = sys.argv[1] if len(sys.argv) > 1 else "app/models/wf/panel_200liq_fresh.npz"
z = np.load(PANEL, allow_pickle=True)
names = [str(x) for x in z["feature_names"]] if "feature_names" in z.files else None
if names is None:
    names = [str(x) for x in z["names"]]
nameset = set(names)
print(f"panel={PANEL} rows={z['X'].shape[0] if 'X' in z.files else '?'} feats={len(names)}")

sys.path.insert(0, "scripts")
import importlib.util

spec = importlib.util.spec_from_file_location("wls", "scripts/wf_label_sweep.py")
wls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wls)
cfg = {c["id"]: c for c in wls.CONFIGS}
for arm in ("CG202_ctl_h5", "CG202_clean_h5", "CG202_clean_dsns_h5"):
    c = cfg.get(arm)
    if not c:
        print(arm, "MISSING CONFIG")
        continue
    ex = c.get("exclude_names") or []
    missing = [n for n in ex if n not in nameset]
    srcs = (c.get("derived") or {}).get("sources") or []
    smissing = [n for n in srcs if n not in nameset]
    print(f"{arm}: exclude={len(ex)} missing={missing} | derived_src={len(srcs)} missing={smissing} | drop_dup={bool(c.get('drop_dup'))}")
