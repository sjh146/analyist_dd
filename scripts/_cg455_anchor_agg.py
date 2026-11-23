#!/usr/bin/env python3
"""CG455 앵커 레벨 집계 — post-train 창의 앵커별 AUC 를 모아 평균±std 와 판정을 낸다."""
from __future__ import annotations

import glob
import json
import os
import statistics
import sys


def main(argv=None):
    argv = argv or sys.argv[1:]
    root = argv[0] if argv else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "services", "xgboost-ml", "reports", "overnight")
    files = sorted(glob.glob(os.path.join(root, "cg455_pt_*.json")))
    rows = []
    skipped = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as exc:
            skipped.append(f"{os.path.basename(f)}: {type(exc).__name__}")
            continue
        folds = d.get("folds") or []
        rows.append({
            "file": os.path.basename(f),
            "anchor": d.get("window_anchor"),
            "auc": d.get("robust_auc"),
            "n_dates": sum(int(x.get("n_dates") or 0) for x in folds),
            "window": ([folds[0].get("window")] if folds else []),
            "rows_scored": d.get("rows_scored"),
        })
    vals = [r["auc"] for r in rows if isinstance(r["auc"], (int, float))]
    out = {"metric": "anchor_level_auc", "n_anchors": len(rows), "anchors": rows,
           "skipped": skipped, "threshold": 0.02}
    if len(vals) >= 2:
        mean = statistics.fmean(vals)
        sd = statistics.pstdev(vals)
        out.update({"mean": round(mean, 4), "std": round(sd, 4),
                    "min": round(min(vals), 4), "max": round(max(vals), 4),
                    "range": round(max(vals) - min(vals), 4)})
        if sd <= 0.010:
            verdict = "앵커 안정(std<=0.010) — post-train AUC 를 기준선 후보로 등록 제안(승인 항목)"
        elif sd > 0.020:
            verdict = "앵커 불안정(std>0.020) — 단일 앵커 AUC 는 기준선이 될 수 없다(축 종결 근거)"
        else:
            verdict = "중간(std 0.010~0.020) — 앵커 평균±std 병기 없이는 판정 불가"
        out["verdict"] = verdict
    else:
        out["verdict"] = "판정불가(유효 앵커 < 2)"
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "cg455_anchor_level.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if len(vals) >= 2 else 3


if __name__ == "__main__":
    raise SystemExit(main())
