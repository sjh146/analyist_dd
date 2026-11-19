#!/usr/bin/env python3
"""CG371 — 창 가드를 낮춰 얻은 추가 5날짜가 '다른 표본'인지 분해한다.

입력: champion_robust_eval --dump-preds jsonl (exp/fold/date/code/y_true/y_pred/fwd_ret)
출력 JSON: 날짜별 AUC 목록, 기존 23날짜(≤2026-11-05) vs 추가 5날짜(≥2026-11-06) 평균·SD·Δ,
          풀링 AUC, 날짜 SD 로 계산한 SE 와 3σ(+0.02) 필요 날짜 수.

판정 규칙(스킬 하드규칙): 폴드 평균·날짜 다중 표본만 쓴다. 단일 날짜로 개선/악화를 말하지 않는다.
"""
import argparse
import json
import math
import statistics


def auc(pairs):
    pos = sum(1 for y, _ in pairs if y == 1)
    neg = len(pairs) - pos
    if pos == 0 or neg == 0:
        return None
    s = sorted(pairs, key=lambda t: t[1])
    ranks = [0.0] * len(s)
    i = 0
    while i < len(s):
        j = i + 1
        while j < len(s) and s[j][1] == s[i][1]:
            j += 1
        r = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = r
        i = j
    rank_sum = sum(ranks[i] for i in range(len(s)) if s[i][0] == 1)
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--split", default="2026-11-06")
    ap.add_argument("--json", required=True)
    a = ap.parse_args()

    by_date = {}
    rows = 0
    with open(a.preds) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            by_date.setdefault(r["date"], []).append((int(r["y_true"]), float(r["y_pred"])))
            rows += 1

    per_date = {}
    for d in sorted(by_date):
        v = per_date_auc = auc(by_date[d])
        if per_date_auc is not None:
            per_date[d] = per_date_auc

    old = {d: v for d, v in per_date.items() if d < a.split}
    new = {d: v for d, v in per_date.items() if d >= a.split}
    allv = list(per_date.values())

    def stat(x):
        vals = list(x.values())
        if not vals:
            return None
        m = statistics.mean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else None
        return {
            "n": len(vals),
            "mean": round(m, 4),
            "sd": round(sd, 4) if sd is not None else None,
            "se": round(sd / math.sqrt(len(vals)), 4) if sd else None,
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
            "pos_frac": round(sum(1 for v in vals if v > 0.5) / len(vals), 3),
        }

    so, sn, sa = stat(old), stat(new), stat(per_date)
    out = {
        "preds": a.preds,
        "rows": rows,
        "dates": len(per_date),
        "split": a.split,
        "old_dates": so,
        "new_dates": sn,
        "all_dates": sa,
        "delta_new_minus_old": (round(sn["mean"] - so["mean"], 4) if so and sn else None),
        "per_date_auc": {d: round(v, 4) for d, v in per_date.items()},
    }
    if sa and sa["sd"]:
        need = int(round((3 * sa["sd"] / 0.02) ** 2))
        out["se_from_sd"] = round(sa["sd"] / math.sqrt(sa["n"]), 4)
        out["dates_needed_3sigma"] = need
        out["dates_remaining"] = max(0, need - sa["n"])
    json.dump(out, open(a.json, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "per_date_auc"}, ensure_ascii=False, indent=1))
    print("per_date:", json.dumps(out["per_date_auc"], ensure_ascii=False))


if __name__ == "__main__":
    main()
