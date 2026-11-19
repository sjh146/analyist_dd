#!/usr/bin/env python3
"""CG369 — 로버스트 평가 덤프 두 개가 **같은 표본**을 채점했는지 검사한다.

WHY (2026-11-20 실측): CG360(09:54Z)과 CG366(18:07Z)은 같은 챔피언·같은 플래그
(--stocks 200 --universe training --universe-seed 0)로 돌았는데, 날짜별 종목 교집합이
**70/200(65% 교체)** 이었다. 원인은 `app/training/universe._default_date_from()` 이
UNIVERSE_ASOF_DATE 미설정 시 `now()-60일` 창을 쓰기 때문 — 적격 집합이 조금만 바뀌어도
`rng.shuffle` 입력 순서가 달라져 선택 200종목이 통째로 재추첨된다.
그 결과 0.5132(CG360) → 0.5349(CG366) 의 Δ+0.0217 은 23번째 날짜가 만든 값이 아니다:
같은 22날짜 안에서도 A전용 130종목 0.5086 vs B전용 130종목 0.5405 (Δ−0.0319) 로,
모집단 교체만으로 사전문턱(+0.02)보다 큰 이동이 생긴다.

사용: python3 scripts/_cg369_repro_check.py --a <dump1.jsonl> --b <dump2.jsonl> [--json out]
판정: overlap_frac == 1.0 · pred_max_abs_delta < 1e-9 · |auc_mean_delta| <= 0.005 이면
      '재현됨(적립 비교 가능)'. 아니면 '재현 안 됨 — 유니버스 동결 필요'.
"""
import argparse
import collections
import json
import statistics as st


def load_dump(path):
    rows = collections.defaultdict(dict)
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            rows[d["date"]][d["code"]] = d
    return rows


def auc(y, p):
    pos = sum(1 for v in y if v == 1)
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return None
    order = sorted(range(len(p)), key=lambda i: p[i])
    ranks = [0.0] * len(p)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and p[order[j + 1]] == p[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    rs = sum(ranks[i] for i in range(len(p)) if y[i] == 1)
    return (rs - pos * (pos + 1) / 2.0) / (pos * neg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    A, B = load_dump(args.a), load_dump(args.b)
    common = sorted(set(A) & set(B))
    per_date = []
    for d in common:
        cs = set(A[d]) & set(B[d])
        only_a = set(A[d]) - set(B[d])
        only_b = set(B[d]) - set(A[d])
        auc_a = auc([A[d][c]["y_true"] for c in sorted(cs)], [A[d][c]["y_pred"] for c in sorted(cs)])
        auc_b = auc([B[d][c]["y_true"] for c in sorted(cs)], [B[d][c]["y_pred"] for c in sorted(cs)])
        auc_oa = auc([A[d][c]["y_true"] for c in sorted(only_a)],
                     [A[d][c]["y_pred"] for c in sorted(only_a)])
        auc_ob = auc([B[d][c]["y_true"] for c in sorted(only_b)],
                     [B[d][c]["y_pred"] for c in sorted(only_b)])
        preds = [abs(A[d][c]["y_pred"] - B[d][c]["y_pred"]) for c in sorted(cs)]
        per_date.append({
            "date": d,
            "n_a": len(A[d]),
            "n_b": len(B[d]),
            "n_common": len(cs),
            "overlap_frac": round(len(cs) / max(1, len(A[d])), 4),
            "auc_common_a": None if auc_a is None else round(auc_a, 4),
            "auc_common_b": None if auc_b is None else round(auc_b, 4),
            "auc_only_a": None if auc_oa is None else round(auc_oa, 4),
            "auc_only_b": None if auc_ob is None else round(auc_ob, 4),
            "pred_max_abs_delta": round(max(preds), 6) if preds else None,
            "pred_n_identical": sum(1 for x in preds if x < 1e-9),
        })

    overlap_frac = st.mean(r["overlap_frac"] for r in per_date) if per_date else 0.0
    deltas = [r["auc_common_b"] - r["auc_common_a"] for r in per_date
              if r["auc_common_a"] is not None and r["auc_common_b"] is not None]
    max_pred = max((r["pred_max_abs_delta"] or 0.0) for r in per_date) if per_date else None
    verdict = ("재현됨 — 적립 날짜 비교 가능" if overlap_frac >= 0.999
               and (max_pred is not None and max_pred < 1e-9)
               and abs(st.mean(deltas)) <= 0.005
               else "재현 안 됨 — 유니버스 동결(UNIVERSE_ASOF_DATE) 필요")

    out = {
        "dump_a": args.a, "dump_b": args.b,
        "n_dates_a": len(A), "n_dates_b": len(B), "n_dates_common": len(common),
        "overlap_frac_mean": round(overlap_frac, 4),
        "pred_max_abs_delta": max_pred,
        "auc_common_delta_mean": round(st.mean(deltas), 4) if deltas else None,
        "auc_common_delta_sd": round(st.stdev(deltas), 4) if len(deltas) > 1 else None,
        "auc_only_a_mean": round(st.mean([r["auc_only_a"] for r in per_date if r["auc_only_a"] is not None]), 4)
        if any(r["auc_only_a"] is not None for r in per_date) else None,
        "auc_only_b_mean": round(st.mean([r["auc_only_b"] for r in per_date if r["auc_only_b"] is not None]), 4)
        if any(r["auc_only_b"] is not None for r in per_date) else None,
        "verdict": verdict,
        "per_date": per_date,
    }
    print(json.dumps({k: v for k, v in out.items() if k != "per_date"}, ensure_ascii=False, indent=1))
    for r in per_date:
        print("  {date} n={n_a}/{n_b} common={n_common} auc_a={auc_common_a} auc_b={auc_common_b} "
              "only_a={auc_only_a} only_b={auc_only_b} id_pred={pred_n_identical}".format(**r))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print("written", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
