import json

contract = set(json.load(open("services/xgboost-ml/app/models/champion/feature_names.json")))
scr = json.load(open("services/xgboost-ml/reports/overnight/cg407_panel_screen.json"))
rows = scr["rows"]
dead = set(scr["dead"]) if scr["dead"] and isinstance(scr["dead"][0], str) else set()
const = set(scr["const"])


def usable(r):
    return (not r.get("stock_constant")) and (not r.get("market_level")) and r["feature"] not in dead


cands = [r for r in rows if usable(r) and r.get("ic_t") is not None]
cands.sort(key=lambda r: -abs(r["ic_t"]))
print("패널 유효(time-varying) 피처:", len(cands), "/ 계약 피처:", len(contract), "/ 계약 미포함:",
      sum(1 for r in cands if r["feature"] not in contract))
print("%-34s %6s %6s %7s %7s %6s %s" % ("feature", "auc", "ic", "ic_t", "fill", "n_obs", "in_contract"))
for r in cands[:15]:
    print("%-34s %6.4f %+6.4f %7.2f %7.2f %6d %s" % (r["feature"], r["auc"], r["ic_mean"], r["ic_t"],
                                                    r["fill"], r["n_obs"], r["feature"] in contract))
print()
hits = [r for r in cands if abs(r["ic_t"]) >= 2.0]
print("|ic_t|>=2.0:", len(hits), "· 그중 계약 미포함:", sum(1 for r in hits if r["feature"] not in contract))
for r in hits[:12]:
    print("  ", r["feature"], round(r["ic_mean"], 4), round(r["ic_t"], 2), "in_contract=", r["feature"] in contract)
