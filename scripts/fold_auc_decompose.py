#!/usr/bin/env python3
"""[CG331] 폴드 AUC 분산 분해 — '폴드 AUC 0.31~0.71' 은 신호 불안정인가, 날짜 표집 잡음인가.

왜: CG330(10폴드×5시드, 같은 패널) 실측에서 같은 config 의 폴드 AUC 가 0.3086~0.7108 로
    폭 0.40 을 벌렸다. 폴드 평균 ±0.02 문턱이 성립하려면 이 분산의 출처를 알아야 한다.
    후보는 ① 폴드마다 들어간 '날짜 집합' 이 다르다(날짜 표집 잡음) ② 날짜별 AUC 가 라벨
    기저율에 따라 움직인다(라벨 구성 효과) ③ 진짜 학습 불안정(시드/초기화).

출력:
  arm 별 ① 폴드별 AUC(날짜평균·풀링) + 폴드 내 날짜 표집 SE
        ② 초과분산 비율 = sd(폴드평균) / mean(폴드 내 SE)  → 1 근처면 '날짜 표집 잡음만'
        ③ 날짜 AUC ~ 라벨 기저율 회귀(기울기·t), 날짜 AUC ~ 종목수
        ④ 날짜 AUC 분포(std·min·max) — 폴드 분산의 하한을 만드는 실체

사용:
  python3 scripts/fold_auc_decompose.py --preds reports/overnight/cg330_preds.jsonl \
      --min-rows 20 --min-pos 3 --min-neg 3 --json-out reports/overnight/cg331_fold_decomp.json
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict


def auc(pairs):
    pos = [p for y, p in pairs if y == 1]
    neg = [p for y, p in pairs if y == 0]
    if not pos or not neg:
        return None
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
    n_pos, n_neg = len(pos), len(neg)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def sd(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def ols(xs, ys):
    n = len(xs)
    if n < 3:
        return {"n": n}
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-9 or sd(xs) < 1e-3:
        return {"n": n, "slope": None, "note": "설명변수 사실상 상수 — 회귀 불가"}  # 분위 라벨은 기저율이 날짜마다 같다
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    resid = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    dof = n - 2
    s2 = sum(r * r for r in resid) / dof if dof > 0 else 0.0
    se = math.sqrt(s2 / sxx) if sxx > 0 else float("nan")
    return {
        "n": n,
        "slope": round(slope, 4),
        "se": round(se, 4),
        "t": round(slope / se, 3) if se and se == se else None,
    }


def load(path):
    """(arm, fold) -> date -> [(y_true, y_pred)]"""
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    bad = 0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                arm = str(d["exp"])
                fold = int(d.get("fold", 0))
                date = str(d["date"])
                y = int(d["y_true"])
                p = float(d["y_pred"])
            except (ValueError, KeyError, TypeError):
                bad += 1
                continue
            data[arm][fold][date].append((y, p))
    return data, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", nargs="+", required=True)
    ap.add_argument("--arms", default=None, help="쉼표구분. 기본=전부")
    ap.add_argument("--min-rows", type=int, default=20)
    ap.add_argument("--min-pos", type=int, default=3)
    ap.add_argument("--min-neg", type=int, default=3)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    wanted = set(a.strip() for a in args.arms.split(",")) if args.arms else None
    merged = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    bad_total = 0
    for path in args.preds:
        data, bad = load(path)
        bad_total += bad
        for arm, folds in data.items():
            if wanted and arm not in wanted:
                continue
            for fold, dates in folds.items():
                for date, rows in dates.items():
                    merged[arm][fold][date].extend(rows)

    out = {"preds": args.preds, "bad_lines": bad_total, "min_rows": args.min_rows,
           "min_pos": args.min_pos, "min_neg": args.min_neg, "arms": {}}

    for arm, folds in sorted(merged.items()):
        date_rows = {}   # date -> (auc, base_rate, n)
        fold_info = {}
        for fold, dates in sorted(folds.items()):
            aucs, bases, ns = [], [], []
            kept_dates = []
            pooled_rows = []
            for date, rows in sorted(dates.items()):
                n_pos = sum(1 for y, _ in rows if y == 1)
                n_neg = len(rows) - n_pos
                if len(rows) < args.min_rows or n_pos < max(1, args.min_pos) or n_neg < max(1, args.min_neg):
                    continue
                a = auc(rows)
                if a is None:
                    continue
                aucs.append(a)
                bases.append(n_pos / len(rows))
                ns.append(len(rows))
                kept_dates.append(date)
                pooled_rows.extend(rows)
                date_rows.setdefault(date, (a, n_pos / len(rows), len(rows)))
            if not aucs:
                fold_info[fold] = {"n_dates": 0}
                continue
            fold_info[fold] = {
                "n_dates": len(aucs),
                "date_from": kept_dates[0] if kept_dates else None,
                "date_to": kept_dates[-1] if kept_dates else None,
                "date_auc_mean": round(sum(aucs) / len(aucs), 4),
                "pooled_auc": round(auc(pooled_rows), 4) if pooled_rows else None,
                "mean_base_rate": round(sum(bases) / len(bases), 4),
                "mean_rows": round(sum(ns) / len(ns), 1),
                "within_fold_se": round(sd(aucs) / math.sqrt(len(aucs)), 4),
                "date_auc_sd": round(sd(aucs), 4),
            }

        fm = [v["date_auc_mean"] for v in fold_info.values() if v.get("n_dates")]
        fse = [v["within_fold_se"] for v in fold_info.values() if v.get("n_dates")]
        if len(fm) < 2:
            out["arms"][arm] = {"folds": fold_info, "note": "폴드 2개 미만 — 분해 불가"}
            continue

        obs_sd = sd(fm)
        mean_se = sum(fse) / len(fse)
        dates_sorted = sorted(date_rows)
        d_auc = [date_rows[d][0] for d in dates_sorted]
        d_base = [date_rows[d][1] for d in dates_sorted]
        d_n = [date_rows[d][2] for d in dates_sorted]
        k = max(1, len(d_auc) // 3)
        terciles = {
            "early": round(sum(d_auc[:k]) / k, 4),
            "mid": round(sum(d_auc[k:2 * k]) / max(1, len(d_auc[k:2 * k])), 4),
            "late": round(sum(d_auc[2 * k:]) / max(1, len(d_auc[2 * k:])), 4),
            "early_range": [dates_sorted[0], dates_sorted[k - 1]],
            "late_range": [dates_sorted[2 * k], dates_sorted[-1]],
        }

        out["arms"][arm] = {
            "folds": fold_info,
            "fold_mean": round(sum(fm) / len(fm), 4),
            "fold_sd": round(obs_sd, 4),
            "fold_min": round(min(fm), 4),
            "fold_max": round(max(fm), 4),
            "fold_se_of_mean": round(obs_sd / math.sqrt(len(fm)), 4),
            "mean_within_fold_se": round(mean_se, 4),
            "excess_dispersion_ratio": round(obs_sd / mean_se, 3) if mean_se else None,
            "n_dates_total": len(dates_sorted),
            "date_auc_sd": round(sd(d_auc), 4),
            "date_auc_min": round(min(d_auc), 4),
            "date_auc_max": round(max(d_auc), 4),
            "reg_date_auc_on_base_rate": ols(d_base, d_auc),
            "reg_date_auc_on_n_rows": ols(d_n, d_auc),
            "reg_date_auc_on_time_index": ols(list(range(len(dates_sorted))), d_auc),
            "tercile_auc": terciles,
            "date_series": [[dates_sorted[i], round(d_auc[i], 4)] for i in range(len(dates_sorted))],
            "date_auc_mean": round(sum(d_auc) / len(d_auc), 4),
            "pooled_auc": round(auc([r for f in folds.values() for rows in f.values() for r in rows]), 4),
        }

    out["reading"] = {
        "excess_dispersion_ratio": "sd(폴드평균)/mean(폴드내 날짜 SE). 1 근처 = 폴드 간 차이가 날짜 표집 잡음과 구분되지 않음(신호 불안정 아님). >2 = 폴드 구성/학습 불안정이 실재.",
        "reg_date_auc_on_base_rate": "날짜 AUC 를 그 날 라벨 기저율로 회귀. |t|>2 면 라벨 구성이 날짜별 AUC 를 움직인다 = 폴드의 라벨 기저율 차이가 폴드 AUC 차이를 만든다.",
    }

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print("saved:", args.json_out)

    for arm, v in out["arms"].items():
        if "fold_mean" not in v:
            print(f"{arm}: {v}")
            continue
        print(f"{arm}: 폴드평균 {v['fold_mean']:.4f} sd {v['fold_sd']:.4f} "
              f"[{v['fold_min']:.3f}~{v['fold_max']:.3f}] · 폴드내 SE {v['mean_within_fold_se']:.4f} "
              f"· 초과분산비 {v['excess_dispersion_ratio']} · 날짜AUC sd {v['date_auc_sd']:.3f}")
        print(f"   날짜AUC~기저율 t {v['reg_date_auc_on_base_rate'].get('t')} "
              f"(slope {v['reg_date_auc_on_base_rate'].get('slope')}) "
              f"· 날짜AUC~종목수 t {v['reg_date_auc_on_n_rows'].get('t')} "
              f"· 시간추세 t {v['reg_date_auc_on_time_index'].get('t')} "
              f"(slope {v['reg_date_auc_on_time_index'].get('slope')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
