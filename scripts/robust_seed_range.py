#!/usr/bin/env python3
"""여러 유니버스 시드의 견고 AUC 요약을 모아 **표집 잡음 범위**를 계산한다.

왜 필요한가 (실측 2026-11-19 CG347):
    champion_robust_eval 은 한 실행이 유니버스 표집 하나(=`--universe-seed`)다. 같은 모델·
    같은 날짜·같은 라벨에서 시드만 0/1/2 로 바꿨더니 0.4887 / 0.5114 / 0.5011 —
    범위 0.0227 로 **사전문턱 +0.02 를 넘었다**. 즉 단일 창에서 '기록 기준선 대비 Δ'는
    문턱과 같은 크기의 표집 잡음 위에 서 있다(해상 불가).
    → 절대 AUC 비교는 다중 시드가 필요하고, arm 비교는 **같은 시드 짝 Δ**로 해야 한다.

이 스크립트는 그 잡음 폭을 수치 한 장으로 남기는 계측기다(모델 팔 실험이 아니다 →
스코어보드 무개선 카운터와 무관).

사용:
  python3 scripts/robust_seed_range.py --inputs a.json b.json c.json \
      --out data/reports/overnight/cg347_seed_range.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True, help="champion_robust_eval 요약 JSON 들")
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=0.02,
                    help="사전 등록 문턱(폴드 평균 Δ)")
    args = ap.parse_args(argv)
    # 호스트 상대경로는 프로젝트 루트 기준으로 만든다(구동기는 PROJ 를 cwd 로 부르지 않는다)
    if not os.path.isabs(args.out):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        args.out = os.path.join(root, args.out)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    seed_aucs, per_date_stds, dates, rows, excluded, seeds_missing = {}, [], set(), set(), [], []
    for p in args.inputs:
        try:
            d = _load(p)
        except (OSError, json.JSONDecodeError) as e:
            seeds_missing.append({"path": p, "error": str(e)[:120]})
            continue
        uni = d.get("universe") or {}
        seed = uni.get("seed")
        key = str(seed if seed is not None else "?")
        auc = d.get("auc_per_date_mean")
        if auc is None:
            auc = d.get("robust_auc")
        if auc is None:
            seeds_missing.append({"path": p, "error": "auc 없음"})
            continue
        seed_aucs[key] = round(float(auc), 6)
        seed_aucs[key + "_pooled"] = d.get("auc_pooled")
        for f in d.get("folds") or []:
            if isinstance(f.get("auc_std"), (int, float)):
                per_date_stds.append(float(f["auc_std"]))
        if d.get("dates_scored"):
            dates.add(int(d["dates_scored"]))
        if d.get("rows_scored"):
            rows.add(int(d["rows_scored"]))
        for w in d.get("windows_excluded") or []:
            excluded.append(w)

    vals = [float(v) for k, v in seed_aucs.items()
            if not k.endswith("_pooled") and isinstance(v, (int, float))]
    if len(vals) < 2:
        out = {"metric": "robust_seed_range", "error": "유효 시드 < 2 — 범위 계산 불가",
               "seed_aucs": seed_aucs, "failed": seeds_missing}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(json.dumps(out, ensure_ascii=False))
        return 2

    mean = statistics.fmean(vals)
    std = statistics.stdev(vals) if len(vals) > 1 else 0.0
    rng = max(vals) - min(vals)
    out = {
        "metric": "robust_seed_range",
        "n_seeds": len(vals),
        "seed_aucs": {k: v for k, v in seed_aucs.items() if not k.endswith("_pooled")},
        "mean": round(mean, 6),
        "std": round(std, 6),
        "range": round(rng, 6),
        "min": round(min(vals), 6),
        "max": round(max(vals), 6),
        "se": round(std / (len(vals) ** 0.5), 6),
        "threshold": args.threshold,
        # 해상 가능 여부의 **보수적** 판정: 시드 범위가 문턱보다 크면 단일 시드 절대 비교는 무효
        "resolvable": bool(rng < args.threshold),
        "range_over_threshold": round(rng / args.threshold, 3) if args.threshold else None,
        "half_range": round(rng / 2.0, 6),
        "dates_scored": sorted(dates),
        "rows_scored": sorted(rows),
        "n_windows_excluded": len(excluded),
        "within_fold_date_auc_std_mean": round(statistics.fmean(per_date_stds), 6) if per_date_stds else None,
        "failed_inputs": seeds_missing,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
