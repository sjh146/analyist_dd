#!/usr/bin/env python3
"""시드 앙상블 축 분석 (추가 학습 없음, 0비용) — 엔지니어 소유.

배경: 판정 지표(`folds[].mean`)는 **폴드별 시드 AUC 의 평균**이다. 그런데 배포되는 모델은
시드/모델 앙상블 예측이다. `wf_label_sweep.py` 는 폴드마다 **시드 평균 확률의 AUC**
(`ens_pooled_auc`)를 이미 기록하므로 두 지표의 갭을 기존 런에서 바로 계산할 수 있다.
이 갭이 곧 "지표가 배포 예측 성능을 얼마나 과소평가하는가"다.

실행: docker exec stock_xgboost_ml python3 /app/scripts/seed_ensemble_gap.py
"""
import json
import sys
from collections import OrderedDict

import numpy as np

PATH = sys.argv[1] if len(sys.argv) > 1 else "/app/reports/overnight/wf_label_sweep.jsonl"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/app/reports/overnight/seed_ensemble_gap.json"

recs = []
with open(PATH) as f:
    for line in f:
        line = line.strip()
        if line:
            recs.append(json.loads(line))

last = OrderedDict()
for r in recs:
    key = r.get("exp_id") or r.get("id") or r.get("exp")
    if key:
        last[key] = r  # 마지막(최신) 기록만

out = []
for k, r in last.items():
    folds = r.get("folds") or {}
    per, ens, daily = [], [], []
    for f in folds.values():
        if f.get("mean") is not None:
            per.append(float(f["mean"]))
        if f.get("ens_pooled_auc") is not None:
            ens.append(float(f["ens_pooled_auc"]))
        if f.get("daily_auc_mean") is not None:
            daily.append(float(f["daily_auc_mean"]))
    if not per:
        continue
    out.append({
        "exp": k, "nfolds": len(per),
        "auc_mean": float(np.mean(per)),
        "auc_std": float(np.std(per)),
        "ens_mean": float(np.mean(ens)) if ens else None,
        "ens_std": float(np.std(ens)) if ens else None,
        "gap": (float(np.mean(ens)) - float(np.mean(per))) if ens else None,
        "daily_mean": float(np.mean(daily)) if daily else None,
        "status": r.get("status"),
    })

out.sort(key=lambda x: x["ens_mean"] if x["ens_mean"] is not None else -1, reverse=True)
print("%-30s %2s  %-14s %-14s %7s %7s %s" % ("exp", "nf", "auc_mean±std", "ens±std", "gap", "daily", "status"))
for x in out:
    ens = ("%.4f±%.4f" % (x["ens_mean"], x["ens_std"])) if x["ens_mean"] is not None else "-"
    gap = ("%+.4f" % x["gap"]) if x["gap"] is not None else "-"
    dm = ("%.4f" % x["daily_mean"]) if x["daily_mean"] is not None else "-"
    print("%-30s %2d  %.4f±%.4f  %-14s %7s %7s %s" % (x["exp"], x["nfolds"], x["auc_mean"],
                                                      x["auc_std"], ens, gap, dm, x["status"]))

gaps = [x["gap"] for x in out if x["gap"] is not None and x["nfolds"] >= 5]
if gaps:
    print()
    print("n=%d runs(≥5폴드) · gap(ens−per_seed) 평균 %+.4f · 중앙값 %+.4f · min %+.4f · max %+.4f"
          % (len(gaps), float(np.mean(gaps)), float(np.median(gaps)), min(gaps), max(gaps)))

with open(OUT, "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("\nwrote %s" % OUT)
