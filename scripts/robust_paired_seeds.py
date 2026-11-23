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


def _pair_key(d, key_by="seed"):
    """짝의 키 — 무엇을 고정하고 비교하는가.

    seed   = 같은 유니버스 표집(CG347: 시드만 바꿔도 절대 AUC 가 0.0227 움직인다)
    anchor = 같은 평가 앵커(CG144: 앵커만 2~7거래일 옮겨도 최대 |Δ| 0.0597 = 사전문턱의 3배).
             CG145 승인(2026-11-21) 이후 승격 판정은 '복수 고정 앵커 평균 짝 Δ' 로 낸다.
    """
    if key_by == "anchor":
        a = d.get("window_anchor")
        return str(a) if a else "?"
    return str((d.get("universe") or {}).get("seed", "?"))


def _load_arm(paths, label, key_by="seed"):
    out = {}
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        seed = _pair_key(d, key_by)
        folds = d.get("folds") or []
        out[seed] = {"auc": round(_auc(d), 6), "path": os.path.basename(p),
                     "model_dir": d.get("model_dir"), "label": label,
                     "window_anchor": d.get("window_anchor"),
                     # 창 서명 — 두 arm 이 같은 날짜창에서 채점됐는지 검사하는 근거
                     "n_folds": len(folds),
                     "windows": [list(f.get("window") or []) for f in folds],
                     # 창(=fold)별 AUC — 시드 간 SE 는 같은 날짜창을 공유해 과소추정되므로
                     # 창 클러스터 분산을 따로 계산한다(2026-11-23 CG432 실측).
                     "fold_aucs": [f.get("auc_mean") for f in folds],
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
    ap.add_argument("--key-by", dest="key_by", choices=("seed", "anchor"), default="seed",
                    help="짝의 키: seed(기본·현행) 또는 anchor(CG145 승인 = 복수 고정 앵커 평균)")
    args = ap.parse_args(argv)

    if not os.path.isabs(args.out):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        args.out = os.path.join(root, args.out)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    a = _load_arm(args.arm_a, args.label_a, args.key_by)
    b = _load_arm(args.arm_b, args.label_b, args.key_by)

    def _order(k):
        """정렬 키 — 시드는 숫자, 앵커는 날짜, 키 없음('?')은 항상 뒤."""
        try:
            return (k == "?", 0, float(k), "")
        except ValueError:
            return (k == "?", 1, 0.0, k)

    seeds = sorted(set(a) & set(b), key=_order)
    deltas = {s: round(a[s]["auc"] - b[s]["auc"], 6) for s in seeds}
    vals = list(deltas.values())
    n = len(vals)
    if n < 2:
        res = {"metric": "robust_paired_seeds", "key_by": args.key_by,
               "error": f"공통 키({args.key_by}) < 2 — 짝 Δ 계산 불가",
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
    # ── 창 클러스터 SE ────────────────────────────────────────────────────────
    # 왜(실측 2026-11-23 CG432): 시드 11개 짝 Δ 평균 +0.0237 · 시드 SE 0.0071 · t 3.35 로
    # '문턱 초과'처럼 보였지만, 창(fold)별로 쪼개면 Δ 가 fold1 −0.0190(3/11) · fold2 −0.0071(5/11)
    # · fold3 +0.0970(11/11) 로 **한 창에 전부 몰려** 있었다. 시드들은 같은 날짜창을 공유하므로
    # 시드 간 독립을 가정한 SE 는 유사반복(pseudo-replication)으로 과소추정된다 → 창을 클러스터로
    # 본 SE 를 함께 기록한다. (판정 문턱/규칙은 이 스크립트에서 바꾸지 않는다 — 리뷰보드 안건.)
    fold_block = []
    cluster = None
    nf = {a[s]["n_folds"] for s in seeds} | {b[s]["n_folds"] for s in seeds}
    if len(nf) == 1 and not fold_mismatch:
        k = nf.pop()
        per_fold = []
        for i in range(k):
            pairs = []
            for s in seeds:
                fa = (a[s]["fold_aucs"] or [None] * k)[i]
                fb = (b[s]["fold_aucs"] or [None] * k)[i]
                if fa is None or fb is None:
                    continue
                pairs.append(float(fa) - float(fb))
            if pairs:
                fm = statistics.fmean(pairs)
                fsd = statistics.stdev(pairs) if len(pairs) > 1 else 0.0
                per_fold.append({"fold": i + 1, "n": len(pairs), "delta_mean": round(fm, 6),
                                 "delta_sd": round(fsd, 6),
                                 "pos": sum(1 for v in pairs if v > 0),
                                 "auc_a": round(statistics.fmean([float(a[s]['fold_aucs'][i]) for s in seeds]), 6),
                                 "auc_b": round(statistics.fmean([float(b[s]['fold_aucs'][i]) for s in seeds]), 6)})
        fold_block = per_fold
        if len(per_fold) >= 2:
            fms = [f["delta_mean"] for f in per_fold]
            cmean = statistics.fmean(fms)
            csd = statistics.stdev(fms)
            cse = csd / (len(fms) ** 0.5)
            cluster = {"n_clusters": len(fms), "fold_mean_deltas": fms,
                       "mean": round(cmean, 6), "sd": round(csd, 6), "se": round(cse, 6),
                       "t": round(cmean / cse, 3) if cse else None,
                       "pos_folds": sum(1 for v in fms if v > 0),
                       "note": "시드 SE 는 같은 날짜창 공유로 과소추정 — 창 클러스터 SE 를 함께 본다"}
    anchor_noise = None
    if args.key_by == "anchor" and n >= 2:
        anchor_noise = {
            "n_anchors": n,
            "delta_sd": round(sd, 6),
            "mean_minus_sd": round(mean - sd, 6),
            "mean_plus_sd": round(mean + sd, 6),
            "gate_mean_minus_sd_ge_threshold": bool((mean - sd) >= args.threshold),
            "pos_anchors": pos,
            "note": "앵커 간 산포를 뺀 보수적 하한이 문턱을 넘는가 — 문턱 재정의는 리뷰보드(CG145)",
        }
    res = {
        "metric": "robust_paired_seeds",
        "key_by": args.key_by,
        "label_a": args.label_a,
        "label_b": args.label_b,
        "n_seeds": n,
        "seeds": seeds,
        "anchor_noise": anchor_noise,
        "auc_a": {s: a[s]["auc"] for s in seeds},
        "auc_b": {s: b[s]["auc"] for s in seeds},
        "deltas": deltas,
        "delta_mean": round(mean, 6),
        "delta_std": round(sd, 6),
        "delta_se": round(se, 6),
        "t": round(t, 3) if t is not None else None,
        "pos_seeds": pos,
        "pos_frac": round(pos / n, 3),
        "folds": fold_block,
        "window_cluster": cluster,
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
