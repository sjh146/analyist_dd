#!/usr/bin/env python3
"""다중 시드(유니버스) 짝 판정용 집계기 — champion_robust_eval JSON 여러 개를 한 판정으로.

WHY(2026-10-01, CG50): 지금까지 arm 비교는 사람이 커맨드 안의 여러 `--out` JSON 을 손으로
짝지어 원장에 넣어야 했고(구동기 `_out_arg` 는 **첫 --out 만** 반환), 그 결과
① 짝 Δ 가 원장에서 사라지고(CG40·CG38·CG47 3회 재발) ② 재현이 불가능했다.
그리고 실측상 **단일 유니버스 Δ 는 증거가 아니다** — 같은 모델·같은 창·같은 프로토콜에서
유니버스 정체(시드)만 바꿔도 폴드평균이 0.5042~0.5439(std 0.0133)로 움직여 사전문턱 +0.02 가
1.50σ 였다(CG48/CG49). 시드를 5개 쓰면 SE ≈ 0.006 이 되어 +0.02 를 3σ 로 검출할 수 있다.

이 스크립트는 **stdlib 만** 쓴다(호스트 python3 에서도 돌아 테스트가 쉽다).

사용(컨테이너 안, cwd=/app):
    python scripts/champion_seed_family_agg.py --agg-out app/reports/cg51_seed_family.json \
        --arm champ app/reports/cg51_champ_s0.json app/reports/cg51_champ_s1.json ... \
        --arm cand  app/reports/cg51_cand_s0.json  app/reports/cg51_cand_s1.json  ...

판정(사전 등록 규칙):
    n < 3                                  → 판정불가(표본 부족, SE 과대 — CG38 교훈)
    delta_mean ≥ threshold and pos == n    → 신호있음
    delta_mean ≥ threshold (부호 불일치)    → 문턱 명목 초과·부호 불일치 → 노이즈
    else                                   → 노이즈
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import datetime


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    folds = [x for x in (d.get("folds") or []) if isinstance(x, dict)]
    return {
        "path": path,
        "measured_at": d.get("measured_at"),
        "model_dir": d.get("model_dir"),
        "protocol": d.get("protocol"),
        "robust_auc": d.get("robust_auc"),
        "auc_std_across_folds": d.get("auc_std_across_folds"),
        "fold_means": [x.get("auc_mean") for x in folds],
        "windows": [x.get("window") for x in folds],
        "auc_pooled": d.get("auc_pooled"),
        "rows_scored": d.get("rows_scored"),
        "universe": (d.get("universe") or {}),
        # 2026-10-02(CG59): 시드 정체 페어링·창/폴드 일관성 검사용.
        # `universe.seed` 는 champion_robust_eval 이 --universe-seed 를 그대로 싣는 값이고,
        # 창 목록은 폴드 구성이 바뀌면 달라진다(두 arm 이 다른 창에서 채점되면 Δ 자체가 무의미).
        "seed": (d.get("universe") or {}).get("seed"),
        "n_folds": len(folds),
        "label_kind": d.get("label_kind"),
    }


def _pair_records(arms: dict, order: list) -> tuple[list, str, list]:
    """두 arm 을 **시드 정체(universe.seed)** 로 페어링한다.

    WHY(CG59): 종전에는 위치(i)로 짝지었다. 시드 JSON 을 순서대로 넘기는 관행에서는
    맞지만, 한쪽 arm 만 실행이 중단돼 시드가 빠지거나 순서가 섞이면 **다른 유니버스끼리**
    Δ 를 재게 된다 — CG55/CG57 계열 실측에서 시드 교체 잡음이 Δ 문턱(+0.02)과 같은
    크기였으므로 이 오배선은 조용히 판정을 뒤집을 수 있다.
    시드가 한쪽이라도 없으면 위치 페어링으로 폴백하고 경고를 남긴다(회귀 0).
    """
    base, ch = order[0], order[1]
    b_seeds = [a["seed"] for a in arms[base]]
    c_seeds = [a["seed"] for a in arms[ch]]
    usable = (all(s is not None for s in b_seeds) and all(s is not None for s in c_seeds)
              and len(set(b_seeds)) == len(b_seeds) and len(set(c_seeds)) == len(c_seeds))
    warned: list = []
    if usable:
        cmap = {a["seed"]: a for a in arms[ch]}
        rows = []
        for a in arms[base]:
            other = cmap.get(a["seed"])
            if other is None:
                warned.append(f"시드 {a['seed']} 가 {ch} arm 에 없다")
                continue
            rows.append((a, other))
        if rows:
            return rows, "universe.seed", warned
        warned.append("시드 교집합이 0 — 위치 페어링으로 폴백")
    else:
        warned.append("시드 정체를 읽을 수 없어 위치 페어링으로 폴백"
                      f"(base seeds={b_seeds[:3]}…, {ch} seeds={c_seeds[:3]}…)")
    n = min(len(arms[base]), len(arms[ch]))
    return [(arms[base][i], arms[ch][i]) for i in range(n)], "positional", warned


def _window_sig(rec: dict):
    try:
        return tuple(tuple(str(x) for x in (w or [])) for w in rec["windows"])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="다중 시드 짝 판정 집계")
    ap.add_argument("--arm", nargs="+", action="append", required=True,
                    metavar=("NAME", "JSON"),
                    help="`--arm <이름> <json> [<json> ...]` — 여러 번 줄 수 있다. "
                         "첫 arm 이 기준(대조군), 두 번째가 판정 대상(챌린저)이다.")
    ap.add_argument("--agg-out", required=True, help="집계 JSON 출력 경로")
    ap.add_argument("--threshold", type=float, default=0.02,
                    help="사전 등록 문턱(폴드평균 Δ, 기본 +0.02)")
    ap.add_argument("--expect-seeds", type=int, default=0, metavar="N",
                    help="기대 시드 수(기본 0=off). 실제 짝 수 n < N 이면 '판정불가' — "
                         "시드가 조용히 빠진 채 '3/3 신호'로 보고되는 것을 막는다(CG59).")
    args = ap.parse_args()

    arms: dict[str, list] = {}
    order: list[str] = []
    for spec in args.arm:
        name, paths = spec[0], spec[1:]
        if not paths:
            print(f"arm {name}: JSON 경로가 없다", file=sys.stderr)
            return 2
        arms[name] = [_load(p) for p in paths]
        order.append(name)
    if len(order) < 2:
        print("arm 이 2개 이상 필요하다(대조군·챌린저)", file=sys.stderr)
        return 2

    def _stat(vs: list) -> dict:
        vals = [float(v) for v in vs if isinstance(v, (int, float))]
        if not vals:
            return {"n": 0}
        return {
            "n": len(vals),
            "mean": round(statistics.mean(vals), 4),
            "std": round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0,
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
            "values": [round(v, 4) for v in vals],
        }

    out_arms = {n: _stat([a["robust_auc"] for a in arms[n]]) for n in order}
    base, ch = order[0], order[1]
    pairs, pairing_mode, pairing_warnings = _pair_records(arms, order)
    for w in pairing_warnings:
        print(f"경고(페어링): {w}", file=sys.stderr)

    rows = []
    ties = 0
    window_mismatch: list = []
    fold_mismatch: list = []
    for i, (b_rec, c_rec) in enumerate(pairs):
        b, c = b_rec["robust_auc"], c_rec["robust_auc"]
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
            continue
        seed = b_rec.get("seed")
        d = float(c) - float(b)
        if abs(d) < 5e-5:          # 4자리 반올림 기준 동점
            ties += 1
        rows.append({"i": i, "seed": seed, "base": round(float(b), 4), "champ": round(float(b), 4),
                     "cand": round(float(c), 4), "delta": round(d, 4)})
        # 창/폴드 일관성 — 두 arm 이 다른 창·다른 폴드 수에서 채점됐으면 Δ 는 무의미하다.
        if b_rec.get("n_folds") != c_rec.get("n_folds"):
            fold_mismatch.append(f"seed {seed}: folds {b_rec.get('n_folds')} vs {c_rec.get('n_folds')}")
        bs, cs = _window_sig(b_rec), _window_sig(c_rec)
        if bs is not None and cs is not None and bs != cs:
            window_mismatch.append(f"seed {seed}: 창 목록 불일치")

    deltas = [r["delta"] for r in rows]
    paired: dict = {"n": len(deltas), "threshold": args.threshold,
                    "base_arm": base, "challenger_arm": ch,
                    "pairing": pairing_mode, "expect_seeds": args.expect_seeds,
                    "n_ties": ties, "pairing_warnings": pairing_warnings,
                    "window_mismatch": window_mismatch, "fold_mismatch": fold_mismatch}
    if len(deltas) >= 2:
        mean = statistics.mean(deltas)
        std = statistics.pstdev(deltas)
        se = std / math.sqrt(len(deltas))
        pos = sum(1 for d in deltas if d > 0)
        paired.update({
            "delta_mean": round(mean, 4), "std": round(std, 4), "se": round(se, 4),
            "t": round(mean / se, 2) if se > 0 else None,
            "pos_seeds": f"{pos}/{len(deltas)}",
            "pos_frac": round(pos / len(deltas), 3),
            "per_seed_delta": deltas,
        })
    elif len(deltas) == 1:
        paired.update({"delta_mean": deltas[0], "std": 0.0, "se": None, "t": None,
                       "pos_seeds": "1/1", "pos_frac": 1.0, "per_seed_delta": deltas})

    tie_note = f" · 동점 {ties}" if ties else ""
    if window_mismatch:
        paired["verdict"] = (f"판정불가(두 arm 의 채점 창이 다르다 — "
                             f"{window_mismatch[0]}). 같은 창에서 다시 재라")
    elif fold_mismatch:
        paired["verdict"] = (f"판정불가(두 arm 의 폴드 수가 다르다 — "
                             f"{fold_mismatch[0]}). 폴드 수가 다른 arm 비교는 무효")
    elif paired.get("n", 0) < 3:
        paired["verdict"] = "판정불가(시드 수 < 3 — SE 과대, 창/시드 수가 부족하면 Δ 를 판정에 쓰지 말라)"
    elif args.expect_seeds and paired["n"] < args.expect_seeds:
        paired["verdict"] = (f"판정불가(기대 시드 수 미달 {paired['n']}<{args.expect_seeds} — "
                             f"중단된 arm 을 완주시킨 뒤 다시 집계하라)")
    elif paired.get("delta_mean", 0) >= args.threshold and paired.get("pos_frac", 0) == 1.0:
        paired["verdict"] = (f"신호있음(Δ{paired['delta_mean']:+.4f} ≥ {args.threshold} · "
                             f"양(+) {paired['pos_seeds']}{tie_note} · SE {paired['se']})")
    elif paired.get("delta_mean", 0) >= args.threshold:
        paired["verdict"] = (f"문턱 명목 초과·부호 불일치 → 노이즈"
                             f"(Δ{paired['delta_mean']:+.4f} · 양(+) {paired['pos_seeds']}{tie_note})")
    else:
        paired["verdict"] = (f"노이즈(Δ{paired.get('delta_mean')} < {args.threshold} · "
                             f"양(+) {paired.get('pos_seeds')}{tie_note})")

    doc = {
        "kind": "champion_seed_family",
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "protocol": arms[order[0]][0].get("protocol"),
        "family": (f"같은 모델·같은 창·같은 라벨에서 유니버스(시드)만 교체 — "
                   f"arm {'·'.join(order)} 각 {len(arms[order[0]])}시드"),
        "arm_names": order,
        "arms": out_arms,
        "seeds": rows,
        "paired": paired,
        "source_jsons": {n: [a["path"] for a in arms[n]] for n in order},
    }
    d = os.path.dirname(args.agg_out)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(args.agg_out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(json.dumps({"agg_out": args.agg_out, "arms": out_arms, "paired": paired},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
