"""풀평균(널 기준선) 이 −1.48%p/세션인 이유를 분해한다 (CG294).

fillable_topk_expectancy 의 함수를 그대로 import 해서 같은 풀을 만들고,
 (a) 있는 그대로 / (b) |gross|>40% 픽 제외 / (c) 세션별 5% 트림 / (d) 필터 off
네 값의 차이로 '극단값이 널을 끌어내렸는지'를 판정한다.
"""
import argparse
import importlib.util
import json
import statistics as st
import sys
from collections import defaultdict

spec = importlib.util.spec_from_file_location("fte", "/app/scripts/fillable_topk_expectancy.py")
fte = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fte)

preds = sys.argv[1] if len(sys.argv) > 1 else "/app/reports/overnight/cg292_q30_preds.jsonl"
tag = sys.argv[2] if len(sys.argv) > 2 else "cg292_q30"
stride = int(sys.argv[3]) if len(sys.argv) > 3 else 5
out_path = sys.argv[4] if len(sys.argv) > 4 else "/app/reports/overnight/cg294_pool_decomp.json"

rows, bad = fte.load_rows(preds, tag)
codes = sorted({r["code"] for r in rows})
dmin, dmax = min(r["date"] for r in rows), max(r["date"] for r in rows)
conn = fte.db_connect()
series = fte.price_series(conn, codes, dmin, dmax)
enr, drop = fte.enrich(rows, series, 5, "close_h")
print(f"rows={len(rows)} enriched={len(enr)} dropped={drop}")

args = argparse.Namespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                          min_price=None, max_price=None)
fa, dropped_f = fte.apply_filters(enr, args, True)
print(f"fillable rows={len(fa)} filtered_out={dropped_f}")
keep = fte.stride_keys(fa, stride)


def pool(rs, keys=None, trim=0.0, drop_abs=None, rt=0.21):
    by = defaultdict(list)
    for r in rs:
        k = (r["fold"], r["date"])
        if keys is not None and k not in keys:
            continue
        v = r.get("gross_pct")
        if v is None:
            continue
        if drop_abs is not None and abs(v) > drop_abs:
            continue
        by[k].append(v - rt)
    out = {}
    for k, vals in by.items():
        if not vals:
            continue
        if trim > 0:
            s = sorted(vals)
            ncut = int(len(s) * trim)
            vals = s[ncut:len(s) - ncut] if ncut else s
        out[str(k[1]) + f"#{k[0]}"] = (len(vals), st.mean(vals))
    return out


variants = {
    "as_is": pool(fa, keep),
    "drop_abs_40": pool(fa, keep, drop_abs=40.0),
    "drop_abs_25": pool(fa, keep, drop_abs=25.0),
    "trim5": pool(fa, keep, trim=0.05),
    "unfiltered": pool(enr, fte.stride_keys(enr, stride)),
}
res = {}
for name, p in variants.items():
    means = [v[1] for v in p.values()]
    res[name] = {"n_sessions": len(means), "mean_pct": round(st.mean(means), 4) if means else None,
                 "median_pct": round(st.median(means), 4) if means else None,
                 "pos_sessions": sum(1 for m in means if m > 0)}
    print(f"{name:12s} sessions={len(means):3d} pool mean {res[name]['mean_pct']:+.4f}%p "
          f"median {res[name]['median_pct']:+.4f} pos {res[name]['pos_sessions']}/{len(means)}")

ext = []
for k in sorted(keep, key=lambda x: (x[1], x[0])):
    rs = [r for r in fa if r["fold"] == k[0] and r["date"] == k[1]]
    vals = sorted((r["gross_pct"], r["code"]) for r in rs if r.get("gross_pct") is not None)
    if not vals:
        continue
    ext.append({"session": k[1], "n": len(vals),
                "worst": [round(vals[0][0], 2), vals[0][1]],
                "best": [round(vals[-1][0], 2), vals[-1][1]],
                "mean": round(st.mean(v for v, _ in vals), 3)})
res["extremes"] = ext
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=1)
print("wrote", out_path)
for e in ext[:8]:
    print("  ", e["session"], "n=", e["n"], "worst", e["worst"], "best", e["best"], "mean", e["mean"])
