#!/usr/bin/env python3
"""CG29 증거 — event_* 16개(주입 대상)의 IC t·관측일수·AUC 를 출력한다."""
import json

d = json.load(open("/app/reports/overnight/panel_screen_420asofpatch.json"))
rows = {r["feature"]: r for r in d["rows"]}
ev = json.load(open("/app/reports/overnight/cg29_lists.json"))
for tag, lst in (("ARM(event)", ev["arm"]), ("PLACEBO", ev["placebo"])):
    print(f"── {tag} ──")
    for f in lst:
        r = rows[f]
        print("  %-30s auc=%-7s |edge|=%-7s ic=%-9s ic_t=%-7s days=%s"
              % (f, r.get("auc"), r.get("auc_abs_edge"), r.get("ic_mean"),
                 r.get("ic_t"), r.get("ic_days")))
