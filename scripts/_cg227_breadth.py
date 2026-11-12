#!/usr/bin/env python3
"""CG227 — h5 초과수익의 '폭(breadth)': top-k 를 넓히면 낙폭 배수가 1.5x 이내로 내려오는가.

왜 (2026-11-13 03:1x 엔지니어 자율 틱, CG224/CG226 후속)
  CG224: h5 는 자본-세션 +0.3000%p(A의 5.8배)지만 비중첩 MDD 3.82x → 경로 변경 불가.
  CG226: 손절 −5/−8% 는 MDD 배수를 3.82x → 3.08/3.39x 로만 낮추고 수익을 −0.54%p 깎았다
  (k=5 기준). 남은 가설은 **선택 폭**이다 — 초과수익이 소수 종목의 이례값에서 나온다면
  k 를 넓힐수록 초과는 풀평균으로 수렴하고 낙폭만 남는다(=신호 없음 확정). 반대로 넓혀도
  자본-세션 우위가 유지되고 낙폭이 분산되면, 그것이 h5 경로를 살리는 유일한 조합이다.

정의(재현 가능):
  · 진입: 세션 종가, 체결성 필터·수수료 왕복은 CG224/CG226 과 동일 경로.
  · h5: 5세션 뒤 종가 청산(손절 없음 = CG224 B 경로) — 손절은 CG226 에서 이미 실패.
  · A(h1): 같은 예측의 상위 k 를 익일 시가 청산.
  · 자본-세션 = A: mean(r1) · B: mean(r5)/5 · MDD 는 비중첩 코호트(5세션 간격).

사전 등록: k ∈ {5,10,20,50} 중 자본-세션 ≥ A(k)+0.05%p AND 비중첩 MDD 배수 ≤1.5x 를
  만족하는 k 가 있으면 '경로 변경 검토 근거'(트레이더 경로 → ②승인). 없으면 h5 축 최종 종결.

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && timeout 900 python -u scripts/_cg227_breadth.py \\
    --preds /app/reports/overnight/cg220_preds.jsonl --tag CO_smooth_d1_h5 --h5 5 \\
    --out /app/reports/overnight/cg227_breadth.json'
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fillable_expectancy import FEE_BUY, FEE_SELL, TAX_SELL  # noqa: E402
from fillable_topk_expectancy import apply_filters, db_connect, enrich, load_rows  # noqa: E402
import _cg226_stop_path as cg226  # noqa: E402  (hl_series·hold_exit·mdd·stat 재사용)

RT = (FEE_BUY + FEE_SELL + TAX_SELL) * 100.0
KS = [5, 10, 20, 50]


def r5_by_k(keep, series, h, k):
    by = {}
    for r in keep:
        by.setdefault(r["date"], []).append(r)
    out = []
    for dt in sorted(by):
        rs = by[dt]
        if len(rs) < 20:
            continue
        vals = []
        for r in sorted(rs, key=lambda x: -x["score"])[:k]:
            got = cg226.hold_exit(series[r["code"]], r["i"], h, None)
            if got is not None:
                vals.append(got[0])
        if vals:
            out.append(float(np.mean(vals)) - RT)
    return out


def r1_by_k(ent_no, k):
    by = {}
    for r in ent_no:
        by.setdefault(r["date"], []).append(r)
    out = []
    for dt in sorted(by):
        rs = by[dt]
        if len(rs) < 20:
            continue
        out.append(float(np.mean([r["gross_pct"] for r in sorted(rs, key=lambda x: -x["score"])[:k]])) - RT)
    return out


def nonoverlap(vals, h):
    n = len(vals) - (len(vals) % h)
    return [float(np.mean(vals[i:i + h])) for i in range(0, n, h)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--h5", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    rows, bad = load_rows(a.preds, a.tag)
    if not rows:
        print(f"[FAIL] 행 없음 tag={a.tag}", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in rows})
    d0 = (pd.Timestamp(min(r["date"] for r in rows)) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(max(r["date"] for r in rows)) + pd.Timedelta(days=6 * (a.h5 + 2))).date()
    conn = db_connect()
    series = cg226.hl_series(conn, codes, d0, d1)

    ent, _ = enrich(rows, series, a.h5, "close_h")
    flt = cg226.SimpleNamespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                                min_price=None, max_price=None)
    ent, _ = apply_filters(ent, flt, on=True)
    keep = []
    for r in ent:
        s = series.get(r["code"])
        try:
            i = s["d"].index(pd.Timestamp(r["date"]).date())
        except (KeyError, ValueError):
            bad += 1
            continue
        if i + a.h5 >= len(s["d"]):
            bad += 1
            continue
        r = dict(r)
        r["i"] = i
        keep.append(r)
    ent_no, _ = enrich(rows, series, 1, "next_open")
    ent_no, _ = apply_filters(ent_no, flt, on=True)

    table = {}
    for k in KS:
        r5 = r5_by_k(keep, series, a.h5, k)
        r1 = r1_by_k(ent_no, k)
        if len(r5) < 30 or len(r1) < 30:
            print(f"[FAIL] k={k} 세션 부족 A={len(r1)} B={len(r5)}", file=sys.stderr)
            return 2
        nov5, nov1 = nonoverlap(r5, a.h5), nonoverlap(r1, a.h5)
        n = min(len(nov5), len(nov1))
        nov5, nov1 = nov5[:n], nov1[:n]
        a_cap = float(np.mean(r1))
        b_cap = float(np.mean(r5)) / a.h5
        a_mdd = cg226.mdd(nov1)
        b_mdd = cg226.mdd(nov5)
        table[k] = {"a_sessions": len(r1), "b_sessions": len(r5),
                    "a_mean": round(a_cap, 4), "b_mean": round(float(np.mean(r5)), 4),
                    "a_capital_session": round(a_cap, 4), "b_capital_session": round(b_cap, 4),
                    "a_mdd_pct": a_mdd, "b_mdd_pct": b_mdd,
                    "mdd_ratio": round(abs(b_mdd) / abs(a_mdd), 2) if a_mdd else None,
                    "n_cohorts": n,
                    "gap_capital_session": round(b_cap - a_cap, 4),
                    "pass": bool(b_cap >= a_cap + 0.05 and a_mdd and abs(b_mdd) <= 1.5 * abs(a_mdd)),
                    "b_stat": cg226.stat(r5)}
    passed = [k for k, v in table.items() if v["pass"]]
    sel = (max(passed, key=lambda k: table[k]["b_capital_session"]) if passed
           else max(table, key=lambda k: table[k]["b_capital_session"]))
    S = table[sel]
    out = {
        "metric_name": "capital_path_compare",
        "tag": a.tag, "k": sel, "h5": a.h5, "fee_roundtrip_pct": round(RT, 4),
        "note": ("breadth 스윕 k∈{5,10,20,50} · 손절 없음(h5 종가청산) · "
                 "path_a/path_b 는 선택 k 의 A(h1)/B(h5)"),
        "selected_variant": f"h5_k{sel}",
        "path_a_h1": {"sessions": S["a_sessions"], "mean": S["a_mean"],
                      "capital_session": S["a_capital_session"], "mdd_pct": S["a_mdd_pct"]},
        "path_b_h5": {"sessions": S["b_sessions"], "mean": S["b_mean"], "sd": S["b_stat"]["sd"],
                      "t": S["b_stat"]["t"], "pos_pct": S["b_stat"]["pos_pct"],
                      "capital_session": S["b_capital_session"], "mdd_pct": S["b_mdd_pct"]},
        "nonoverlap_compare": {"n_cohorts": S["n_cohorts"],
                               "a_mdd_pct": S["a_mdd_pct"], "b_mdd_pct": S["b_mdd_pct"],
                               "mdd_ratio": S["mdd_ratio"],
                               "a_mean_pct": S["a_mean"], "b_mean_pct": S["b_mean"]},
        "breadth": {str(k): v for k, v in table.items()},
        "pass_ks": passed,
        "window": {"first": min(r["date"] for r in rows), "last": max(r["date"] for r in rows)},
        "rows_bad": bad,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    for k, v in table.items():
        print(f"k={k:2d} A {v['a_capital_session']:+.4f} MDD {v['a_mdd_pct']:8.3f} | "
              f"h5 {v['b_mean']:+.4f}%p/5s cap {v['b_capital_session']:+.4f} MDD "
              f"{v['b_mdd_pct']:8.3f} 배수 {v['mdd_ratio']}x Δcap {v['gap_capital_session']:+.4f} "
              f"{'PASS' if v['pass'] else ''}")
    print(f"선택 k={sel} · 통과 {passed or '없음'}")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
