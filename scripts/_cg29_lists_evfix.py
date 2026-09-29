#!/usr/bin/env python3
"""CG29 arm/placebo 확정 (evfix 패널 기준).

규칙(사전 등록):
  · ARM  = evfix 패널에서 백필 이벤트가 **살아 있는** 컬럼 — 전체 nonzero ≥ 0.5%(=68행 이상).
           (분산 0 인 컬럼은 어차피 학습창 std 필터에서 탈락하므로 넣어도 측정되지 않는다.)
  · PLACEBO = 비-event 무정보 피처를 같은 개수만큼. 무정보 = 패널 스크린에서
              시간가변·비종목상수·비시장레벨이면서 |AUC−0.5|+|IC| 최소군.
"""
import json

import numpy as np

P = "/app/app/models/wf/panel_420_asofpatch_evfix.npz"
d = np.load(P, allow_pickle=True)
names = [str(x) for x in d["feature_names"]]
X = np.asarray(d["X"], dtype=np.float64)

event_cols = [p["name"] for p in
              json.load(open("/app/reports/overnight/patch_panel_events_backfill.json"))["patched"]]
cov = {}
seen = {}
for j, n in enumerate(names):
    seen.setdefault(n, j)
for n in event_cols:
    col = X[:, seen[n]]
    cov[n] = float(100.0 * np.mean(col != 0))
arm = sorted([n for n in event_cols if cov[n] >= 0.5], key=lambda n: -cov[n])
print("(A) event 컬럼 커버리지(전체 nonzero%):")
for n in sorted(event_cols, key=lambda n: -cov[n]):
    print("   %-30s %6.2f%%  %s" % (n, cov[n], "ARM" if n in arm else "제외(<0.5%)"))
print("ARM %d개:" % len(arm), arm)

scr = json.load(open("/app/reports/overnight/panel_screen_420asofpatch.json"))
rows = {r["feature"]: r for r in scr["rows"]}
dead = set(scr["dead"])
prev = set(json.load(open("/app/reports/overnight/cg29_lists.json"))["placebo"])
pool = [f for f in dead if not f.startswith("event_") and f in set(names)
        and rows[f].get("n_obs") and rows[f]["n_obs"] >= 5000
        and not rows[f].get("stock_constant") and not rows[f].get("market_level")]
pool.sort(key=lambda f: abs(rows[f]["auc_abs_edge"] or 0.0) + abs(rows[f]["ic_mean"] or 0.0))
placebo = pool[:len(arm)]
print("\n(B) PLACEBO %d개:" % len(placebo))
for f in placebo:
    print("   %-30s auc=%s ic=%s" % (f, rows[f].get("auc"), rows[f].get("ic_mean")))

json.dump({"panel": P, "arm": arm, "arm_coverage_pct": {n: round(cov[n], 2) for n in arm},
           "placebo": placebo},
          open("/app/reports/overnight/cg29_lists_evfix.json", "w"), ensure_ascii=False, indent=1)
print("\nwrote /app/reports/overnight/cg29_lists_evfix.json")
