#!/usr/bin/env python3
"""CG29 진단 — event_* 16개가 폴드 학습창에서 분산 0 으로 탈락하는지 확인한다.

스모크 실측(2026-09-29 16:07): CO_core30_e16_h5 가
`RuntimeError: 파생 피처가 필터를 통과하지 못했다 — 측정 무효` 로 실패
(= gate_add 로 넣은 파생 목록이 `cols = np.std(Xtr, axis=0) > 0` 필터를 전부 통과하지 못함).
"""
import json

import numpy as np

PANEL = "/app/app/models/wf/panel_420_asofpatch.npz"
d = np.load(PANEL, allow_pickle=True)
names = [str(x) for x in d["feature_names"]]
X = np.asarray(d["X"], dtype=np.float64)
dates = np.asarray(d["dates"]).astype(str)
print("panel", X.shape, "unique names", len(set(names)))
ev = json.load(open("/app/reports/overnight/cg29_lists.json"))
arm, placebo = ev["arm"], ev["placebo"]

idx = {}
seen = {}
for j, n in enumerate(names):
    seen.setdefault(n, j)  # 첫 등장 열(코드의 이름→열 매핑과 동일하게 첫 열로 가정)
for n in arm + placebo:
    idx[n] = seen[n]

dd = sorted(set(dates))
print("dates", dd[0], "~", dd[-1], len(dd))
for folds in (2, 5):
    step = len(dd) // folds
    print(f"── folds={folds} (step={step}) ──")
    for i in range(1, folds):
        cut = dd[step * i - 1]
        tr = dates <= cut
        zero_ev = [n for n in arm if float(np.std(X[tr, idx[n]])) == 0.0]
        zero_pl = [n for n in placebo if float(np.std(X[tr, idx[n]])) == 0.0]
        print(f"  fold{i} cut={cut} train={int(tr.sum())}행 | event 분산0 {len(zero_ev)}/{len(arm)} "
              f"| placebo 분산0 {len(zero_pl)}/{len(placebo)}")
        if i == 1:
            print("    event 분산0:", zero_ev)
            for n in arm:
                col = X[tr, idx[n]]
                print("      %-30s train nonzero%%=%5.1f  전체 nonzero%%=%5.1f"
                      % (n, 100 * np.mean(col != 0), 100 * np.mean(X[:, idx[n]] != 0)))
