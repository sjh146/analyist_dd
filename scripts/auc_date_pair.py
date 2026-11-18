#!/usr/bin/env python3
"""[CG328] 날짜 단위 짝 AUC — 폴드 5개로 집계한 짝 Δ 의 검정력 부족을 직접 측정한다.

왜: CG325 실측에서 라벨·피처 arm 의 폴드 Δstd = 0.0555 → 5폴드 SE = 0.0248.
    사전문턱 +0.02 가 1 SE 안쪽이라 "노이즈" 판정이 검정력 부족 때문인지 신호 부재 때문인지
    구분할 수 없었다. 폴드는 사실 날짜 묶음일 뿐이므로, 날짜 하나를 한 단위로 짝지으면
    단위 수가 5 → n_dates 로 늘어나 SE 가 줄어든다(날짜 간 상관이 크면 실효 n 은 줄지만 측정 가능).

사용:
  python3 scripts/auc_date_pair.py --preds A.jsonl [B.jsonl ...] \
      --arm-a LS_quant_q30_h5 --arm-b CG324_maskq_h5 --min-rows 10 --json-out out.json

출력: arm 별 ① 풀링 AUC ② 폴드 단위 짝 Δ(기존 판정 방식) ③ 날짜 단위 짝 Δ(신규)
      — mean/median/std/SE/t/양수비율/부호검정 p 를 함께 낸다.
설계 규칙: 짝은 **날짜 교집합**만 쓴다는 사실을 출력에 남긴다(다른 평가창 오독 방지, CG318).
"""
import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict


def load(path):
    """exp -> date -> [(y_true, y_pred)]"""
    data = defaultdict(lambda: defaultdict(list))
    bad = 0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                exp = d["exp"]
                date = str(d["date"])
                y = int(d["y_true"])
                p = float(d["y_pred"])
            except (ValueError, KeyError, TypeError):
                bad += 1
                continue
            data[exp][date].append((y, p))
    return data, bad


def auc(pairs):
    """Mann-Whitney AUC with tie handling (0.5 credit for ties)."""
    pos = [p for y, p in pairs if y == 1]
    neg = [p for y, p in pairs if y == 0]
    if not pos or not neg:
        return None
    # rank-based: O(n log n)
    allv = sorted(((p, y) for y, p in pairs), key=lambda t: t[0])
    ranks = [0.0] * len(allv)
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    rank_sum_pos = sum(r for r, (v, y) in zip(ranks, allv) if y == 1)
    n_pos = len(pos)
    n_neg = len(neg)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def pooled_auc(by_date, min_rows):
    pairs = []
    for date, rows in by_date.items():
        if len(rows) < min_rows:
            continue
        pairs.extend(rows)
    return auc(pairs) if pairs else None


def date_aucs(by_date, min_rows, min_pos=0, min_neg=0):
    out = {}
    for date, rows in by_date.items():
        if len(rows) < min_rows:
            continue
        n_pos = sum(1 for y, _ in rows if y == 1)
        n_neg = len(rows) - n_pos
        if n_pos < max(1, min_pos) or n_neg < max(1, min_neg):
            continue
        a = auc(rows)
        if a is not None:
            out[date] = a
    return out


def stats(xs):
    n = len(xs)
    if n == 0:
        return {"n": 0}
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    se = sd / math.sqrt(n) if n > 1 else float("nan")
    sxs = sorted(xs)
    med = sxs[n // 2] if n % 2 else (sxs[n // 2 - 1] + sxs[n // 2]) / 2
    pos = sum(1 for x in xs if x > 0)
    neg = sum(1 for x in xs if x < 0)
    return {
        "n": n,
        "mean": round(mean, 6),
        "median": round(med, 6),
        "sd": round(sd, 6),
        "se": None if se != se else round(se, 6),
        "t": None if se in (0.0, float("nan")) or se != se else round(mean / se, 4),
        "pos": pos,
        "neg": neg,
        "pos_frac": round(pos / n, 4),
        "min": round(sxs[0], 6),
        "max": round(sxs[-1], 6),
    }


def sign_test_p(pos, neg):
    """two-sided exact binomial on (pos, neg) ignoring ties."""
    n = pos + neg
    if n == 0:
        return None
    k = min(pos, neg)
    # P(X <= k) * 2 with p=0.5
    c = 0.0
    for i in range(0, k + 1):
        c += math.comb(n, i)
    p = 2.0 * c / (2.0 ** n)
    return round(min(1.0, p), 5)


def paired(a_map, b_map):
    common = sorted(set(a_map) & set(b_map))
    deltas = [b_map[d] - a_map[d] for d in common]
    return common, deltas


def fold_pairs(a_map, b_map, fold_of):
    """기존 판정 방식: 폴드별 AUC 평균 → 폴드 단위 짝 Δ."""
    fa, fb = defaultdict(list), defaultdict(list)
    for d, v in a_map.items():
        if d in fold_of:
            fa[fold_of[d]].append(v)
    for d, v in b_map.items():
        if d in fold_of:
            fb[fold_of[d]].append(v)
    deltas = []
    for f in sorted(set(fa) & set(fb)):
        ma = sum(fa[f]) / len(fa[f])
        mb = sum(fb[f]) / len(fb[f])
        deltas.append(mb - ma)
    return deltas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", nargs="+", required=True)
    ap.add_argument("--arm-a", required=True, help="대조군(control)")
    ap.add_argument("--arm-b", required=True, help="시험군(arm)")
    ap.add_argument("--min-rows", type=int, default=10)
    ap.add_argument("--min-pos", type=int, default=0)
    ap.add_argument("--min-neg", type=int, default=0)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--bootstrap", type=int, default=2000, help="날짜 부트스트랩 반복")
    args = ap.parse_args()

    data = defaultdict(lambda: defaultdict(list))
    fold_of = {}
    total_bad = 0
    for path in args.preds:
        d, bad = load(path)
        total_bad += bad
        for exp, by_date in d.items():
            for date, rows in by_date.items():
                data[exp][date].extend(rows)
        # fold 매핑(같은 런이면 date->fold 는 일관)
        with open(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                fold_of.setdefault(str(r.get("date")), r.get("fold"))

    for arm in (args.arm_a, args.arm_b):
        if arm not in data:
            sys.exit(f"arm 없음: {arm} (있는 arm: {sorted(data)})")

    res = {"arms": {}, "pair": {}, "protocol": {
        "unit_primary": "date",
        "pair_key": "date 교집합만",
        "min_rows_per_date": args.min_rows,
        "min_pos_per_date": args.min_pos,
        "min_neg_per_date": args.min_neg,
        "auc": "Mann-Whitney with tie credit",
    }, "bad_lines": total_bad}

    da = {}
    for arm in (args.arm_a, args.arm_b):
        by_date = data[arm]
        a_map = date_aucs(by_date, args.min_rows, args.min_pos, args.min_neg)
        da[arm] = a_map
        res["arms"][arm] = {
            "dates": len(a_map),
            "pooled_auc": (lambda v: None if v is None else round(v, 6))(pooled_auc(by_date, args.min_rows)),
            "date_auc": stats(list(a_map.values())),
        }

    common, deltas = paired(da[args.arm_a], da[args.arm_b])
    st = stats(deltas)
    st["sign_test_p"] = sign_test_p(st.get("pos", 0), st.get("neg", 0))
    res["pair"]["date"] = {
        "n_pairs": len(common),
        "delta": st,
        "first": common[0] if common else None,
        "last": common[-1] if common else None,
    }

    fd = fold_pairs(da[args.arm_a], da[args.arm_b], fold_of)
    fst = stats(fd)
    fst["sign_test_p"] = sign_test_p(fst.get("pos", 0), fst.get("neg", 0))
    res["pair"]["fold_legacy"] = {"n_pairs": len(fd), "delta": fst}

    # 날짜 부트스트랩: Δ 평균의 95% 구간(날짜 재표집)
    if deltas and args.bootstrap > 0:
        rnd = random.Random(20261118)
        n = len(deltas)
        means = []
        for _ in range(args.bootstrap):
            s = 0.0
            for _ in range(n):
                s += deltas[rnd.randrange(n)]
            means.append(s / n)
        means.sort()
        res["pair"]["date"]["bootstrap95"] = [
            round(means[int(0.025 * len(means))], 6),
            round(means[int(0.975 * len(means))], 6),
        ]

    print(json.dumps(res, ensure_ascii=False, indent=2))
    if args.json_out:
        os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
        with open(args.json_out, "w") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
