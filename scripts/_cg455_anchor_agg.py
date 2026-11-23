#!/usr/bin/env python3
"""CG455 앵커 레벨 집계 — post-train 창의 앵커별 AUC 를 모아 평균±std 와 판정을 낸다.

수리(실측 2026-11-24 00:40): 종전 기본 출력 경로가 `services/xgboost-ml/reports/overnight/`
였는데 그 디렉터리는 **컨테이너(root) 소유**라 호스트 사용자(jhshi)가 쓸 수 없다 →
`PermissionError: [Errno 13]` 로 17.8분 측정 5개가 원장에 '판정불가'로만 남았다
(CG455 실측: 앵커 5개 JSON 은 전부 rc=0 으로 생성됐는데 집계 파일만 못 썼다).
그래서 **입력은 컨테이너 산출 디렉터리, 출력은 쓰기 가능한 `data/reports/overnight/`** 로 분리한다.

부가 실측 주의: champion_robust_eval 은 `--dates-per-fold` 상한까지 창을 **누적 확장**하므로
앵커를 뒤로 옮기면 표본 창이 길어진다(앵커 6개 = 중첩 창). 따라서 여기서 나오는 std 는
'앵커 선택 잡음'과 '창 길이 효과'가 섞인 값이다 — `nested_windows` 로 표시해 보고에 병기한다.
"""
from __future__ import annotations

import glob
import json
import os
import statistics
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_IN = os.path.join(PROJ, "services", "xgboost-ml", "reports", "overnight")
DEF_OUT = os.path.join(PROJ, "data", "reports", "overnight")


def main(argv=None):
    argv = argv or sys.argv[1:]
    src = argv[0] if argv else DEF_IN
    out_dir = argv[1] if len(argv) > 1 else DEF_OUT
    files = sorted(glob.glob(os.path.join(src, "cg455_pt_*.json")))
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
            "auc_pooled": d.get("auc_pooled"),
            "n_dates": sum(int(x.get("n_dates") or 0) for x in folds),
            "window": ([folds[0].get("window")] if folds else []),
            "rows_scored": d.get("rows_scored"),
            "train_start": d.get("train_start"),
            "train_end": d.get("train_end"),
        })
    rows.sort(key=lambda r: str(r.get("anchor") or ""))
    vals = [r["auc"] for r in rows if isinstance(r["auc"], (int, float))]
    out = {"metric": "anchor_level_auc", "n_anchors": len(rows), "anchors": rows,
           "skipped": skipped, "threshold": 0.02,
           "source_dir": src}
    if len(vals) >= 2:
        mean = statistics.fmean(vals)
        sd = statistics.pstdev(vals)
        out.update({"mean": round(mean, 4), "std": round(sd, 4),
                    "min": round(min(vals), 4), "max": round(max(vals), 4),
                    "range": round(max(vals) - min(vals), 4)})
        lens = sorted({r["n_dates"] for r in rows})
        out["nested_windows"] = len(lens) > 1 and all(
            a["window"] and b["window"] and a["window"][0][0] == b["window"][0][0]
            for a in rows for b in rows)
        out["window_date_counts"] = lens
        if sd <= 0.010:
            verdict = "앵커 안정(std<=0.010) — post-train AUC 를 기준선 후보로 등록 제안(승인 항목)"
        elif sd > 0.020:
            verdict = "앵커 불안정(std>0.020) — 단일 앵커 AUC 는 기준선이 될 수 없다(축 종결 근거)"
        else:
            verdict = "중간(std 0.010~0.020) — 앵커 평균±std 병기 없이는 판정 불가"
        if out.get("nested_windows"):
            verdict += " · ⚠중첩 창(앵커=창 상한) — std 에 창 길이 효과 포함"
        out["verdict"] = verdict
    else:
        out["verdict"] = "판정불가(유효 앵커 < 2)"
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, "cg455_anchor_level.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    out["written_to"] = dest
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if len(vals) >= 2 else 3


if __name__ == "__main__":
    raise SystemExit(main())
