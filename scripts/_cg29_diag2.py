#!/usr/bin/env python3
"""CG29 진단 2 — ev 패널에 **추가된** event 컬럼(idx 210~226)의 월별 커버리지를 본다.

발견(진단 1): 기본 패널의 event_* 16개는 2026-04 이전 학습창에서 **전부 분산 0**(전량 0)이다.
즉 워크포워드 폴드 1~2 는 이 피처군을 아예 볼 수 없다. 그 원인이 ①패널이 낡아서(백필 이전 빌드)
인지 ②원천 자체가 최근 구간만 있는지 를 ev 패널의 추가 컬럼과 대조해 가른다.
"""
import json

import numpy as np

ev = np.load("/app/app/models/wf/panel_420_asofpatch_ev.npz", allow_pickle=True)
names = [str(x) for x in ev["feature_names"]]
X = np.asarray(ev["X"], dtype=np.float64)
dates = np.asarray(ev["dates"]).astype(str)
added = names[210:]
print("ev 패널 추가 컬럼", len(added))
months = {}
for dt in dates:
    months.setdefault(dt[:7], 0)
    months[dt[:7]] += 1
mk = sorted(months)
print("월별 행수:", {k: months[k] for k in mk})

hdr = "%-30s " % "feature" + " ".join("%6s" % k[2:] for k in mk)
print(hdr)
for j in range(210, X.shape[1]):
    n = names[j]
    cells = []
    for k in mk:
        m = np.char.startswith(dates, k)
        cells.append("%5.1f%%" % (100 * np.mean(X[m, j] != 0)))
    print("%-30s " % n + " ".join("%6s" % c for c in cells))

# 기본 패널 event 컬럼과의 대조(같은 월 구간에서 커버리지가 다른가)
b = np.load("/app/app/models/wf/panel_420_asofpatch.npz", allow_pickle=True)
bn = [str(x) for x in b["feature_names"]]
bX = np.asarray(b["X"], dtype=np.float64)
print("\n기본 패널 event_exec_change_5d / 이벤트 합계 vs ev 추가 컬럼 합계 (월별 nonzero 비율)")
for k in mk:
    m = np.char.startswith(dates, k)
    base_j = bn.index("event_exec_change_5d")
    print("  %s  base(exe)=%5.1f%%  ev_added(event_exec idx215)=%5.1f%%"
          % (k, 100 * np.mean(bX[m, base_j] != 0), 100 * np.mean(X[m, names.index("event_exec_change_5d") if False else 215] != 0)))
