#!/usr/bin/env python3
"""CG29 arm/placebo 목록 확정 — 기본 패널(panel_420_asofpatch)의 event_* 컬럼 중
'시간가변 & 종목상수 아님 & 시장레벨 아님' 을 arm 으로, 같은 개수의 비-event 무정보 피처를
placebo 로 고른다(용량 교란 통제). 결과는 JSON 으로 출력해 config 에 그대로 붙인다.
"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

d = json.load(open("/app/reports/overnight/panel_screen_420asofpatch.json"))
rows = {r["feature"]: r for r in d["rows"]}
dead = set(d["dead"])
event = sorted([f for f in rows if f.startswith("event_")])

usable = [f for f in event
          if rows[f].get("n_obs") and rows[f]["n_obs"] >= 5000
          and not rows[f].get("stock_constant") and not rows[f].get("market_level")]
skipped = [f for f in event if f not in usable]
print("event_* 총 %d · 주입가능(시간가변·비종목상수·비시장레벨) %d" % (len(event), len(usable)))
for f in event:
    r = rows[f]
    tag = "ARM" if f in usable else "skip"
    print("  %-6s %-30s auc=%s ic=%s n=%s const=%s mkt=%s dead=%s"
          % (tag, f, r.get("auc"), r.get("ic_mean"), r.get("n_obs"),
             r.get("stock_constant"), r.get("market_level"), f in dead))
print("skip 사유(종목상수/시장레벨/표본부족):", skipped)

n = len(usable)
pool = [f for f in dead if not f.startswith("event_")
        and not f.endswith("_5d")
        and rows[f].get("n_obs") and rows[f]["n_obs"] >= 5000
        and not rows[f].get("stock_constant") and not rows[f].get("market_level")]
pool.sort(key=lambda f: abs(rows[f]["auc_abs_edge"] or 0.0) + abs(rows[f]["ic_mean"] or 0.0))
placebo = pool[:n]
print("\nplacebo %d개(비-event 무정보 최소군):" % len(placebo))
for f in placebo:
    r = rows[f]
    print("  %-30s auc=%s ic=%s" % (f, r.get("auc"), r.get("ic_mean")))

json.dump({"event_all": event, "arm": usable, "skipped": skipped, "placebo": placebo},
          open("/app/reports/overnight/cg29_lists.json", "w"), ensure_ascii=False, indent=1)
print("\nwrote /app/reports/overnight/cg29_lists.json")
