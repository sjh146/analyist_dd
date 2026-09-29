#!/usr/bin/env python3
"""CG29 placebo 후보 선정 — 패널 스크린에서 '무정보'이면서 CORE_FEATURES(48) 밖인 피처를 뽑는다."""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")
import scripts.train_curated as tc  # noqa: E402

core = set(tc.CORE_FEATURES)
d = json.load(open("/app/reports/overnight/panel_screen_420asofpatch.json"))
rows = {r["feature"]: r for r in d["rows"]}
dead = sorted(set(d["dead"]))
print("dead total", len(dead), "| dead IN core:", len([f for f in dead if f in core]))
cand = [f for f in dead if f not in core
        and rows[f].get("n_obs") and rows[f]["n_obs"] >= 5000
        and not rows[f].get("stock_constant") and not rows[f].get("market_level")]
cand.sort(key=lambda f: abs(rows[f]["auc_abs_edge"] or 0.0) + abs(rows[f]["ic_mean"] or 0.0))
print("lowest-info dead not-in-core, time-varying stock-level (top 25):")
for f in cand[:25]:
    r = rows[f]
    print("  %-34s auc=%s ic=%s n=%s const=%s mkt=%s"
          % (f, r["auc"], r["ic_mean"], r["n_obs"], r["stock_constant"], r["market_level"]))
