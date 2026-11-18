#!/usr/bin/env python3
"""두 arm(모델 디렉터리)을 **같은 유니버스 시드**로 짝지어 Δ 분포를 계산한다.

왜 (실측 2026-11-19 CG347): champion_robust_eval 의 절대 AUC 는 유니버스 표집(seed)만 바꿔도
0.4887→0.5114 (범위 0.0227) 움직인다 = 사전문턱 +0.02 와 동급. 따라서 arm 비교는
① 같은 시드로 두 arm 을 각각 채점하고 ② 시드별 짝 Δ 를 만든 뒤 ③ Δ 평균·SE·부호일치를 봐야 한다.
시드 잡음은 짝 안에서 상쇄된다(절대값 비교 금지).

사용:
  python3 scripts/robust_paired_seeds.py \
      --arm-a reports/overnight/cg348_champion_s0.json reports/overnight/cg348_champion_s1.json ... \
      --arm-b reports/overnight/cg348_retrain_cg346_s0.json ... \
      --label-a champion --label-b retrain_cg346 \
      --out data/reports/overnight/cg348_paired.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys


def _auc(d):
    v = d.get("auc_per_date_mean")
    return float(v if v is not None else d.get("robust_auc"))


def _load_arm(paths, label):
    out = {}
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        seed = str((d.get("universe") or {}).get("seed", "?"))
        folds = d.get("folds") or []
        out[seed] = {"auc": round(_auc(d), 6), "path": os.path.basename(p),
                     "model_dir": d.get("model_dir"), "label": label,
                     # 창 서명 — 두 arm 이 같은 날짜창에서 채점됐는지 검사하는 근거
                     "n_folds": len(folds),
                     "windows": [list(f.get("window") or []) for f in folds],
                     "train_end": d.get("train_end")}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-a", nargs="+", required=True)
    ap.add_argument("--arm-b", nargs="+", required=True)
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=0.02)
    args = ap.parse_args(argv)

    if not os.path.isabs(args.out):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        args.out = os.path.join(root, args.out)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    a = _load_arm(args.arm_a, args.label_a)
    b = _load_arm(args.arm_b, args.label_b)
    seeds = sorted(set(a) & set(b), key=lambda s: (s == "?", s))
    deltas = {s: round(a[s]["auc"] - b[s]["auc"], 6) for s in seeds}
    vals = list(deltas.values())
    n = len(vals)
    if n < 2:
        res = {"metric": "robust_paired_seeds", "error": "공통 시드 < 2 — 짝 Δ 계산 불가",
               "arm_a": a, "arm_b": b}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(json.dumps(res, ensure_ascii=False))
        return 2

    mean = statistics.fmean(vals)
    sd = statistics.stdev(vals)
    se = sd / (n ** 0.5)
    t = mean / se if se else None
    pos = sum(1 for v in vals if v > 0)
    # 창 불일치 검사 — 실측(2026-11-19 CG348): train_end 메타가 arm 마다 달라(챔피언 2026-10-01
    # vs 후보 2026-08-27) OOS 창이 **다른 날짜 구간**이 됐다(챔피언 2026-01~05 vs 후보 2026-09~11).
    # 같은 시드로 짝지어도 채점 대상이 다르면 Δ 는 무의미하다 → 그런 요약은 판정하지 않는다.
    window_mismatch = [f"seed {s}: {a[s]['windows']} vs {b[s]['windows']}"
                       for s in seeds if a[s]["windows"] != b[s]["windows"]]
    fold_mismatch = [f"seed {s}: {a[s]['n_folds']} vs {b[s]['n_folds']} 폴드"
                     for s in seeds if a[s]["n_folds"] != b[s]["n_folds"]]
    res = {
        "metric": "robust_paired_seeds",
        "label_a": args.label_a,
        "label_b": args.label_b,
        "n_seeds": n,
        "seeds": seeds,
        "auc_a": {s: a[s]["auc"] for s in seeds},
        "auc_b": {s: b[s]["auc"] for s in seeds},
        "deltas": deltas,
        "delta_mean": round(mean, 6),
        "delta_std": round(sd, 6),
        "delta_se": round(se, 6),
        "t": round(t, 3) if t is not None else None,
        "pos_seeds": pos,
        "pos_frac": round(pos / n, 3),
        "threshold": args.threshold,
        "window_mismatch": window_mismatch,
        "fold_mismatch": fold_mismatch,
        "meets_threshold": bool(mean >= args.threshold and pos == n and not window_mismatch),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
