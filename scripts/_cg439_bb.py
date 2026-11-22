import json

print("%4s %9s %7s %9s %8s %9s %9s" % ("seed", "delta", "t_iid", "t_block", "p_1s", "d_vs_ctl", "t_ctl_blk"))
acc, acctl = [], []
for s in range(5):
    d = json.load(open("/app/reports/overnight/cg439_money_s%d_bb.json" % s))
    f = d["conditions"]["fillable"]
    pk = f["baseline_pool"]["paired_by_k"]["5"]
    kk = f["k"]["5"]
    ob = pk.get("overlap_boot") or {}
    oc = kk["paired"].get("overlap_boot") or {}
    acc.append((pk["delta_mean"], ob.get("t")))
    acctl.append((kk["paired"]["delta_mean"], oc.get("t")))
    print("%4d %+9.3f %7.2f %9s %8s %+9.3f %9s"
          % (s, pk["delta_mean"], pk["t"], ob.get("t"), ob.get("p_one_sided"),
             kk["paired"]["delta_mean"], oc.get("t")))
print()
print("vs 풀평균: Δ평균 %+.3f · iid t 평균 %.2f · 블록 t 평균 %.2f · 블록 t≥2 시드 %d/5"
      % (sum(x[0] for x in acc) / 5, 0, sum(x[1] for x in acc if x[1] is not None) / 5,
         sum(1 for x in acc if x[1] is not None and x[1] >= 2)))
print("vs retrain: Δ평균 %+.3f · 블록 t 평균 %.2f · 블록 t≥2 시드 %d/5"
      % (sum(x[0] for x in acctl) / 5, sum(x[1] for x in acctl if x[1] is not None) / 5,
         sum(1 for x in acctl if x[1] is not None and x[1] >= 2)))
