#!/usr/bin/env python3
"""예측 점수 시계열 지속성 진단 (CG265, 2026-11-14).

동기: 로버스트 AUC 는 계속 0.55 근처인데 돈 지표(체결가능 순기대)는 음수다.
AUC 는 '그날의 횡단면 순위'만 재고, 매매는 '며칠에 걸친 선택의 연속'이다.
모델 점수가 종목별로 날마다 뒤집히면(낮은 lag-1 자기상관·낮은 top-k 겹침)
+0.02 AUC 는 회전비용·노이즈만 남기고 사라진다. 이 스크립트는 그 다리를 잰다.

입력: wf_label_sweep.py --dump-preds 산출 JSONL
      (필드: exp, fold, date, code, y_true, y_pred, fwd_ret)

출력(JSON):
  lag1_spearman_mean   종목 간 점수 순위의 날짜 간 상관(일별 계산 후 평균)
  lag1_code_spearman   종목별 점수의 날짜 간 상관(종목별 계산 후 평균) — 같은 것을 반대 축에서
  topk_overlap         k별 연속일 top-k 겹침 비율(무작위 기대 k/N 대비 배수)
  n_dates / n_codes / rows

판정 기준(사전 등록): lag1_spearman_mean < 0.50 이면 '점수 비지속'으로 기록하고,
예측 시계열 스무딩(3일 trailing) arm 을 다음 실험으로 등록한다. >= 0.50 이면 축 종결.

사용:
  python3 scripts/score_persistence.py --preds data/reports/cg265_preds.jsonl \
      --out data/reports/cg265_persistence.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from collections import defaultdict

import numpy as np


def _rank(a: np.ndarray) -> np.ndarray:
    order = a.argsort()
    r = np.empty(len(a), dtype=float)
    r[order] = np.arange(len(a), dtype=float)
    # 동점 평균화
    _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    if (cnt > 1).any():
        sums = np.zeros(len(cnt))
        np.add.at(sums, inv, r)
        r = (sums / cnt)[inv]
    return r


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return float("nan")
    ra, rb = _rank(a), _rank(b)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    d = np.sqrt((ra * ra).sum() * (rb * rb).sum())
    return float((ra * rb).sum() / d) if d > 0 else float("nan")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True, help="--dump-preds JSONL 경로")
    ap.add_argument("--out", default=None, help="결과 JSON 경로")
    ap.add_argument("--topk", default="3,5,10", help="겹침 계산할 k 목록(쉼표)")
    ap.add_argument("--exp", default=None, help="특정 exp 만 사용(기본: 최다 행 exp)")
    args = ap.parse_args(argv)

    by_exp: dict[str, list] = defaultdict(list)
    with open(args.preds) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            by_exp[str(r.get("exp", ""))].append(r)
    if not by_exp:
        print("preds 가 비어 있다", file=sys.stderr)
        return 2
    exp = args.exp or max(by_exp, key=lambda k: len(by_exp[k]))
    recs = by_exp[exp]

    # date -> code -> [p, fwd]
    per_date: dict[str, dict[str, float]] = defaultdict(dict)
    per_code: dict[str, dict[str, float]] = defaultdict(dict)
    for r in recs:
        d, c = str(r["date"]), str(r["code"])
        p = float(r["y_pred"])
        per_date[d][c] = p
        per_code[c][d] = p
    dates = sorted(per_date)
    ks = [int(x) for x in str(args.topk).split(",") if x.strip()]

    # 1) 날짜 간 횡단면 순위 상관(공통 종목만)
    xs: list[float] = []
    for d0, d1 in zip(dates, dates[1:]):
        common = sorted(set(per_date[d0]) & set(per_date[d1]))
        if len(common) >= 3:
            xs.append(spearman(np.array([per_date[d0][c] for c in common]),
                               np.array([per_date[d1][c] for c in common])))
    xs = [x for x in xs if np.isfinite(x)]

    # 2) 종목별 시계열 자기상관
    cs: list[float] = []
    for c, series in per_code.items():
        ds = sorted(series)
        if len(ds) < 3:
            continue
        a = np.array([series[d] for d in ds[:-1]])
        b = np.array([series[d] for d in ds[1:]])
        v = spearman(a, b)
        if np.isfinite(v):
            cs.append(v)

    # 3) top-k 겹침(연속일)
    overlap: dict[str, dict] = {}
    n_codes = len(per_code)
    for k in ks:
        ovs, rand = [], (k / max(n_codes, 1))
        for d0, d1 in zip(dates, dates[1:]):
            t0 = set(sorted(per_date[d0], key=per_date[d0].get, reverse=True)[:k])
            t1 = set(sorted(per_date[d1], key=per_date[d1].get, reverse=True)[:k])
            if t0 and t1:
                ovs.append(len(t0 & t1) / float(k))
        m = float(np.mean(ovs)) if ovs else float("nan")
        overlap[f"top{k}"] = {"mean": round(m, 4) if np.isfinite(m) else None,
                              "random_expected": round(rand, 4),
                              "lift_vs_random": round(m / rand, 2) if ovs and rand > 0 else None,
                              "n_pairs": len(ovs)}

    out = {
        "preds": args.preds, "exp": exp, "rows": len(recs),
        "n_dates": len(dates), "n_codes": n_codes,
        "lag1_spearman_mean": round(float(np.mean(xs)), 4) if xs else None,
        "lag1_spearman_std": round(float(np.std(xs)), 4) if xs else None,
        "lag1_spearman_min": round(float(np.min(xs)), 4) if xs else None,
        "lag1_pairs": len(xs),
        "lag1_code_spearman_mean": round(float(np.mean(cs)), 4) if cs else None,
        "lag1_code_spearman_median": round(float(np.median(cs)), 4) if cs else None,
        "topk_overlap": overlap,
        "verdict": ("점수 비지속(스무딩 arm 등록)" if xs and float(np.mean(xs)) < 0.50
                    else "점수 지속(축 종결)" if xs else "판정불가"),
    }
    txt = json.dumps(out, ensure_ascii=False, indent=1)
    print(txt)
    if args.out:
        dp = os.path.dirname(args.out)
        if dp:
            os.makedirs(dp, exist_ok=True)
        with open(args.out, "w") as f:
            f.write(txt + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
