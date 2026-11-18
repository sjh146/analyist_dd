#!/usr/bin/env python3
"""[CG329] 호라이즌 앙상블 평가 — 여러 arm 의 '날짜내 백분위 순위'를 평균낸 블렌드가
단일 arm 보다 나은가. 학습을 다시 돌리지 않고 dump 된 예측만으로 계산한다.

왜: 라벨 호라이즌(h5/h8/h10)은 개별로는 h8 Δ−0.0338·h10 미측정이었고, 모델 앙상블(가중·모델집합)은
    Δ+0.0004 였다. 그러나 **호라이즌을 섞은 순위 앙상블**은 측정된 적이 없다. 호라이즌이 다르면
    라벨 잡음의 상관이 낮아져 평균 순위의 AUC 가 개별보다 올라갈 수 있다.

계산: 같은 (date, code) 교집합에서 arm 별 백분위 순위(0~1, 동률 평균) → 산술평균 = blend score
      → 날짜별 AUC → 대조 arm 과 날짜 짝 Δ (auc_date_pair 와 같은 규약: 날짜 교집합만, 부호검정 포함)

사용:
  python3 scripts/auc_blend_eval.py --preds dump.jsonl \
     --arms LS_quant_q30_h5,LS_quant_q30_h8,LS_quant_q30_h10 --ref LS_quant_q30_h5 \
     --min-rows 20 --min-pos 3 --min-neg 3 --json-out out.json
"""
import argparse
import json
import math
from collections import defaultdict


def load(path):
    rows = defaultdict(dict)  # (date) -> {code: (y, {exp: pred})}
    exps = set()
    bad = 0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                date = str(d["date"])
                code = str(d["code"])
                exp = d["exp"]
                y = int(d["y_true"])
                p = float(d["y_pred"])
            except (ValueError, KeyError, TypeError):
                bad += 1
                continue
            rec = rows[date].get(code)
            if rec is None:
                rec = {"y": y, "preds": {}}
                rows[date][code] = rec
            rec["preds"][exp] = p
            exps.add(exp)
    return rows, exps, bad


def pct_rank(values):
    """values: list of floats -> percentile rank 0..1 (평균동률)"""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2.0
        for k in range(i, j + 1):
            out[order[k]] = rank / (n - 1) if n > 1 else 0.5
        i = j + 1
    return out


def auc(pairs):
    pos = [p for y, p in pairs if y == 1]
    neg = [p for y, p in pairs if y == 0]
    if not pos or not neg:
        return None
    allv = sorted(pairs, key=lambda t: t[1])
    ranks = [0.0] * len(allv)
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][1] == allv[i][1]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    rsp = sum(r for r, (y, _) in zip(ranks, allv) if y == 1)
    npos, nneg = len(pos), len(neg)
    return (rsp - npos * (npos + 1) / 2.0) / (npos * nneg)


def stats(xs):
    n = len(xs)
    if n == 0:
        return {"n": 0}
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else float("nan")
    pos = sum(1 for x in xs if x > 0)
    neg = sum(1 for x in xs if x < 0)
    return {"n": n, "mean": round(mean, 6), "sd": round(sd, 6),
            "se": None if se != se else round(se, 6),
            "t": None if (se != se or se == 0) else round(mean / se, 4),
            "pos": pos, "neg": neg, "pos_frac": round(pos / n, 4),
            "min": round(min(xs), 6), "max": round(max(xs), 6),
            "sign_test_p": sign_p(pos, neg)}


def sign_p(pos, neg):
    n = pos + neg
    if n == 0:
        return None
    k = min(pos, neg)
    c = sum(math.comb(n, i) for i in range(k + 1))
    return round(min(1.0, 2.0 * c / (2.0 ** n)), 5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--arms", required=True, help="쉼표 구분 — 블렌드에 넣을 arm 들")
    ap.add_argument("--ref", required=True, help="대조 arm(보통 h5)")
    ap.add_argument("--min-rows", type=int, default=20)
    ap.add_argument("--min-pos", type=int, default=3)
    ap.add_argument("--min-neg", type=int, default=3)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    rows, exps, bad = load(args.preds)
    missing = [a for a in arms + [args.ref] if a not in exps]
    if missing:
        raise SystemExit(f"arm 없음: {missing} (있는 arm: {sorted(exps)})")

    per_date = defaultdict(lambda: defaultdict(list))  # date -> name -> [(y,score)]
    kept = dropped = 0
    for date, codes in rows.items():
        # 모든 arm + ref 가 있는 code 만
        use = [c for c, r in codes.items() if all(a in r["preds"] for a in set(arms + [args.ref]))]
        if len(use) < args.min_rows:
            dropped += 1
            continue
        ys = [codes[c]["y"] for c in use]
        npos = sum(ys)
        nneg = len(ys) - npos
        if npos < args.min_pos or nneg < args.min_neg:
            dropped += 1
            continue
        kept += 1
        for a in arms + [args.ref]:
            vals = [codes[c]["preds"][a] for c in use]
            pr = pct_rank(vals)
            for c, y, s in zip(use, ys, pr):
                per_date[date][a].append((y, s))
        blend = [0.0] * len(use)
        for a in arms:
            pr = pct_rank([codes[c]["preds"][a] for c in use])
            for i, v in enumerate(pr):
                blend[i] += v / len(arms)
        for c, y, s in zip(use, ys, blend):
            per_date[date]["__blend__"].append((y, s))

    names = arms + [args.ref, "__blend__"]
    out = {"arms": {}, "preds": args.preds, "dates_kept": kept, "dates_dropped": dropped,
           "bad_lines": bad, "blend_of": arms, "ref": args.ref,
           "rule": "날짜내 백분위 순위 평균(동률 평균) · 날짜 교집합만 · AUC Mann-Whitney"}

    date_auc = defaultdict(dict)
    for date, d in per_date.items():
        for n in names:
            if n in d:
                a = auc(d[n])
                if a is not None:
                    date_auc[n][date] = a

    for n in names:
        vals = list(date_auc[n].values())
        out["arms"][n] = {"dates": len(vals), "date_auc": stats(vals)}

    for n in names:
        if n == args.ref:
            continue
        common = sorted(set(date_auc[n]) & set(date_auc[args.ref]))
        deltas = [date_auc[n][d] - date_auc[args.ref][d] for d in common]
        out.setdefault("paired_vs_ref", {})[n] = stats(deltas)
        out["paired_vs_ref"][n]["n_pairs"] = len(common)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
