#!/usr/bin/env python3
"""CG456 집계 — post-train 창의 앵커별 짝 Δ(재학습 후보 − 챔피언) 평균±std 를 낸다.

왜 짝인가(CG455 실측 2026-11-24): 같은 모델·같은 학습구간에서 창 상한만 옮겨도 절대 AUC 가
0.4790~0.5889(범위 0.1099)로 흔들렸다. 즉 단일 앵커의 절대값 비교는 앵커 잡음(문턱의 5.5배)에
묻힌다. 두 모델을 **같은 앵커**에서 함께 재면 앵커 공통분이 상쇄되어 Δ 의 std 만 남는다.

출력은 쓰기 가능한 `data/reports/overnight/cg456_paired_anchors.json`
(컨테이너 산출 디렉터리는 root 소유 → CG455 에서 PermissionError 실측).
"""
from __future__ import annotations

import glob
import json
import os
import re
import statistics
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_IN = os.path.join(PROJ, "services", "xgboost-ml", "reports", "overnight")
DEF_OUT = os.path.join(PROJ, "data", "reports", "overnight")
ARMS = ("champion", "retrain_cg346")
PAT = re.compile(r"cg456_(?P<arm>[A-Za-z0-9_]+)_(?P<anchor>\d{4}-\d{2}-\d{2})\.json$")


def main(argv=None):
    argv = argv or sys.argv[1:]
    src = argv[0] if argv else DEF_IN
    out_dir = argv[1] if len(argv) > 1 else DEF_OUT
    grid = {}
    skipped = []
    for f in sorted(glob.glob(os.path.join(src, "cg456_*.json"))):
        mt = PAT.search(os.path.basename(f))
        if not mt:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as exc:
            skipped.append(f"{os.path.basename(f)}: {type(exc).__name__}")
            continue
        folds = d.get("folds") or []
        grid.setdefault(mt.group("anchor"), {})[mt.group("arm")] = {
            "auc": d.get("robust_auc"),
            "auc_pooled": d.get("auc_pooled"),
            "n_dates": sum(int(x.get("n_dates") or 0) for x in folds),
            "rows_scored": d.get("rows_scored"),
            "window": (folds[0].get("window") if folds else None),
        }
    paired, unpaired = [], []
    for anchor in sorted(grid):
        a, b = grid[anchor].get(ARMS[0]), grid[anchor].get(ARMS[1])
        if not a or not b or not isinstance(a.get("auc"), (int, float)) \
                or not isinstance(b.get("auc"), (int, float)):
            unpaired.append({"anchor": anchor, "have": sorted(grid[anchor])})
            continue
        if (a.get("n_dates") or 0) != (b.get("n_dates") or 0):
            unpaired.append({"anchor": anchor, "reason": "n_dates 불일치",
                             "champion": a.get("n_dates"), ARMS[1]: b.get("n_dates")})
            continue
        paired.append({"anchor": anchor, "champion": a["auc"], ARMS[1]: b["auc"],
                       "delta": round(b["auc"] - a["auc"], 4),
                       "n_dates": a.get("n_dates"), "window": a.get("window"),
                       "challenger_pooled": b.get("auc_pooled"),
                       "champion_pooled": a.get("auc_pooled")})
    deltas = [p["delta"] for p in paired]
    out = {"metric": "anchor_paired_delta", "arm": ARMS[1], "control": ARMS[0],
           "n_paired_anchors": len(paired), "paired": paired, "unpaired": unpaired,
           "skipped": skipped, "threshold": 0.02, "source_dir": src}
    if deltas:
        out["delta_mean"] = round(statistics.fmean(deltas), 4)
        out["delta_std"] = round(statistics.pstdev(deltas), 4) if len(deltas) > 1 else None
        out["n_positive"] = sum(1 for x in deltas if x > 0)
        out["min"] = min(deltas)
        out["max"] = max(deltas)
        out["abs_champion_mean"] = round(statistics.fmean(
            [p["champion"] for p in paired]), 4)
        out["abs_challenger_mean"] = round(statistics.fmean(
            [p[ARMS[1]] for p in paired]), 4)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, "cg456_paired_anchors.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    out["written_to"] = dest
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if len(paired) >= 2 else 3


if __name__ == "__main__":
    raise SystemExit(main())
