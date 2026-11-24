#!/usr/bin/env python3
"""짝 Δ 잡음바닥 계측기 (CG462) — 원장이 아니라 **스윕 요약 JSON** 을 읽는다.

무엇: 같은 런(=같은 요약 파일) 안의 여러 arm 중 대조군('ctl' 포함)과 나머지 arm 을
**폴드별로 짝지어** Δ 를 만들고, 폴드 짝 Δ 의 sd·SE·검출가능 최소효과(MDE)를 낸다.

왜: 스킬 판정규칙은 '폴드 std ±0.03 이므로 폴드 평균 +0.02 만 신호'라고 가정한다.
그러나 판정 통계량은 arm 레벨(fold std)이 아니라 두 arm 의 **짝 Δ**다. 폴드 짝 Δ 의
SE 를 실측하면 '+0.02 문턱이 검출 가능한가(검정력)'를 정면으로 답할 수 있다.
레벨 std 0.03 은 '서로 다른 런을 비교할 때'의 잡음이고, 같은 런·같은 폴드의 짝 Δ 는
그보다 훨씬 작을 수 있다 — 그러면 '22사이클 미달'은 검출력 부족이 아니라 실효 0 이다.

읽기 전용 · 새 런 없음. 산출: data/reports/overnight/cg462_paired_delta_noise.json
"""
from __future__ import annotations
import glob
import json
import statistics as st

DIRS = ("/home/jhshi/analyist_dd/services/xgboost-ml/reports/overnight/*.json",
        "/home/jhshi/analyist_dd/data/reports/overnight/*.json")


def fold_means(exp):
    fo = exp.get("folds")
    if isinstance(fo, dict):
        out = []
        for k in sorted(fo):
            v = fo[k]
            if isinstance(v, dict) and isinstance(v.get("mean"), (int, float)):
                out.append((k, float(v["mean"]), v.get("test_from"), v.get("test_to")))
        return out
    if isinstance(fo, list):
        out = []
        for i, v in enumerate(fo):
            if isinstance(v, dict) and isinstance(v.get("auc_mean"), (int, float)):
                out.append((f"fold{i+1}", float(v["auc_mean"]), v.get("window"), None))
            elif isinstance(v, (int, float)):
                out.append((f"fold{i+1}", float(v), None, None))
        return out
    return []


def main() -> int:
    pairs, runs, skipped = [], 0, []
    for pat in DIRS:
        for p in sorted(glob.glob(pat)):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception as exc:
                skipped.append((p, type(exc).__name__))
                continue
            res = d.get("results") if isinstance(d, dict) else None
            if not isinstance(res, list) or len(res) < 2:
                continue
            exps = {}
            for e in res:
                if not isinstance(e, dict) or not e.get("exp"):
                    continue
                fm = fold_means(e)
                if len(fm) >= 3 and e.get("status", "ok") == "ok":
                    exps[e["exp"]] = dict(fm=[x[1] for x in fm], span=[(x[2], x[3]) for x in fm], desc=e.get("desc"))
            if len(exps) < 2:
                continue
            runs += 1
            ctl = [k for k in exps if "ctl" in k.lower() or "control" in k.lower()]
            if not ctl:
                continue
            ctl = ctl[0]
            base = exps[ctl]["fm"]
            for a, v in exps.items():
                if a == ctl or len(v["fm"]) != len(base):
                    continue
                diffs = [round(b - c, 4) for b, c in zip(v["fm"], base)]
                L = len(diffs)
                m = st.mean(diffs)
                sd = st.stdev(diffs) if L > 1 else 0.0
                se = sd / (L ** 0.5)
                pairs.append(dict(file=p.split("/")[-1], arm=a, ctl=ctl, n=L, mean=round(m, 4),
                                  sd=round(sd, 4), se=round(se, 4),
                                  t=round(m / se, 2) if se > 0 else None, diffs=diffs,
                                  arm_folds=[round(x, 4) for x in v["fm"]],
                                  ctl_folds=[round(x, 4) for x in base]))

    print(f"스윕 요약 파일 중 다arm 런 {runs} · 대조군 있는 짝 {len(pairs)} · 파싱실패 {len(skipped)}")
    if skipped:
        print("  파싱실패 예:", skipped[:3])
    for n in sorted({x["n"] for x in pairs}):
        sub = [x for x in pairs if x["n"] == n]
        sds = sorted(x["sd"] for x in sub)
        ses = sorted(x["se"] for x in sub)
        print(f"n={n}: pairs {len(sub)} · 폴드Δ sd median {st.median(sds):.4f} p75 {sds[3*len(sds)//4]:.4f} max {sds[-1]:.4f}"
              f" · SE median {st.median(ses):.4f} max {ses[-1]:.4f} · MDE(2.8×SE) {2.8*st.median(ses):.4f}")
    if pairs:
        sig = [x for x in pairs if x["t"] is not None and abs(x["t"]) >= 2]
        print(f"|t|>=2 인 짝: {len(sig)}/{len(pairs)} · |Δ|>=0.02 인 짝: {sum(1 for x in pairs if abs(x['mean'])>=0.02)}")
        print(f"짝 Δ 평균의 중앙값 {st.median([x['mean'] for x in pairs]):+.4f} · |Δ| 중앙값 {st.median([abs(x['mean']) for x in pairs]):.4f}")
    print("--- |t| 상위 10 ---")
    for x in sorted(pairs, key=lambda y: -abs(y["t"] or 0))[:10]:
        print(f"{x['file'][:38]:38s} {x['arm'][:28]:28s} n{x['n']} Δ{x['mean']:+.4f} sd{x['sd']:.4f} t{x['t']}")
    print("--- |Δ|>=0.02 인데 |t|<2 (레벨 잡음이 만든 '신호') ---")
    for x in [y for y in pairs if abs(y["mean"]) >= 0.02 and (y["t"] or 0) < 2][:10]:
        print(f"{x['file'][:38]:38s} {x['arm'][:28]:28s} Δ{x['mean']:+.4f} sd{x['sd']:.4f} t{x['t']} diffs{x['diffs']}")
    out = "/home/jhshi/analyist_dd/data/reports/overnight/cg462_paired_delta_noise.json"
    json.dump({"metric": "paired_fold_delta_noise", "n_pairs": len(pairs), "pairs": pairs},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
