#!/usr/bin/env python3
"""CG47 결과 집계 — 6개 JSON(시드 0/1/2 × 챔피언/챌린저)을 파싱해 짝 Δ 를 계산한다.

구동기는 champion_robust_eval 항목에서 첫 `--out` 만 판정하므로(짝 Δ 를 못 만든다), 이 스크립트가
판정 산출물이다. 출력을 그대로 원장 `parsed.paired` 에 실어라(per_exp 금지 — 스코어보드가 arm
최고값으로 오독한다, CG31 사고).
"""
import json
import os
import statistics
import sys

REP = "services/xgboost-ml/app/reports"
SEEDS = (0, 1, 2)


def load(seed, kind):
    p = os.path.join(REP, f"cg47_s{seed}_{kind}.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def main():
    rows, deltas = [], []
    for s in SEEDS:
        c, d = load(s, "champ"), load(s, "cand")
        if not c or not d:
            print(f"seed {s}: 파일 없음 (champ={bool(c)} cand={bool(d)})")
            continue
        folds_c = [round(w["auc_mean"], 4) for w in c["folds"]]
        folds_d = [round(w["auc_mean"], 4) for w in d["folds"]]
        pair = [round(y - x, 4) for x, y in zip(folds_c, folds_d)]
        delta = round(d["robust_auc"] - c["robust_auc"], 4)
        deltas.append(delta)
        rows.append({"seed": s, "champ": c["robust_auc"], "cand": d["robust_auc"], "delta": delta,
                     "champ_folds": folds_c, "cand_folds": folds_d, "paired_folds": pair,
                     "universe_overlap_c": c.get("universe", {}).get("overlap_train200")})
        print(f"seed {s}: 챔피언 {c['robust_auc']:.4f} {folds_c}  챌린저 {d['robust_auc']:.4f} {folds_d}"
              f"  → Δ{delta:+.4f}  창별짝 {pair}")

    if not deltas:
        print("집계 불가 — 산출물 없음")
        return 1

    mean = statistics.fmean(deltas)
    pos = sum(1 for d in deltas if d > 0)
    sd = statistics.stdev(deltas) if len(deltas) > 1 else 0.0
    se = sd / (len(deltas) ** 0.5) if deltas else 0.0
    print(f"\n짝 Δ 평균 {mean:+.4f} · 양(+) {pos}/{len(deltas)} · 최악 {min(deltas):+.4f} · "
          f"sd {sd:.4f} · SE {se:.4f}")
    if mean >= 0.02 and pos == len(deltas):
        print("판정: 유니버스에 견고한 챌린저 우위 → 승격 검토(별도 승인·dry-run 부터)")
    elif pos == len(deltas):
        print("판정: 방향은 일관되나 문턱 미달 → 노이즈(축 유지, 문턱 조정은 승인 대상)")
    else:
        print("판정: 부호 뒤섞임 — 유니버스 정체 잡음이 지배(+0.02 문턱이 검출 한계 이하)")

    out = {"deltas": deltas, "mean": round(mean, 4), "pos": f"{pos}/{len(deltas)}",
           "worst": min(deltas), "sd": round(sd, 4), "se": round(se, 4), "per_seed": rows}
    with open(os.path.join(REP, "cg47_pairs_summary.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n요약 저장: {REP}/cg47_pairs_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
