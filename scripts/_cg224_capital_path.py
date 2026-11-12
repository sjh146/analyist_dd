#!/usr/bin/env python3
"""CG224 — 운용 경로 비교: 현행 h1(익일 시가 청산) vs h5(5세션 보유)의 **자본효율·낙폭**.

왜 (2026-11-13 02:2x 엔지니어 자율 틱)
  h5 축은 이제 4관문을 통과했다(CG220 5시드·8폴드 / CG221 변동성 교란 기각 / CG222 체결성 필터 /
  CG223 시간 강건). 남은 질문은 **'돈이 더 되는가'가 아니라 '같은 자본으로 더 버는가'**다.
  h5 는 자본을 5세션 묶어 두므로, 세션당 Δ 를 그대로 비교하면 5배 과대평가된다.
  자본-세션(capital-session) 단위로 정규화하고, 낙폭(MDD)을 함께 재야 경로 변경 근거가 된다.

정의(재현·검증 가능하게 못박음):
  · A(현행 경로): 매 세션 top-k 매수(그날 종가) → **익일 시가** 청산. 자본 전액 회전.
  · B(h5 경로): 매 세션 top-k 매수(그날 종가) → **5세션 뒤 종가** 청산. 5코호트 분할 = 자본 1/5씩.
  · capital-session 기대 = A: mean(r1) · B: mean(r5)/5  ← 자본을 1세션 묶어 둔 단위당 수익
  · MDD 는 **비중첩 코호트**(5세션 간격, n≈48) 자본곡선에서 계산. 두 경로를 같은 간격으로 비교.
  · 체결성 필터·수수료 왕복은 fillable_topk_expectancy 와 동일 경로(상한가·일변동 25%·거래대금 10억).

사전 등록: B의 capital-session 기대가 A보다 +0.05%p 이상 높고 B의 MDD가 A의 1.5배 이내 →
  '경로 변경 검토 근거'(트레이더 소유 → ②승인). 아니면 'A 유지'(경로 변경 불필요).
재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && timeout 900 python -u scripts/_cg224_capital_path.py \
    --preds /app/reports/overnight/cg220_preds.jsonl --tag CO_smooth_d1_h5 --k 5 --h5 5 \
    --out /app/reports/overnight/cg224_path.json'
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fillable_expectancy import FEE_BUY, FEE_SELL, TAX_SELL  # noqa: E402
from fillable_topk_expectancy import (  # noqa: E402
    apply_filters, db_connect, enrich, load_rows, price_series,
)

RT = (FEE_BUY + FEE_SELL + TAX_SELL) * 100.0


def stat(v):
    v = [x for x in v if x is not None]
    n = len(v)
    if n < 2:
        return {"n": n}
    m = float(np.mean(v))
    sd = float(np.std(v, ddof=1))
    return {"n": n, "mean": round(m, 4), "sd": round(sd, 4),
            "t": round(m / (sd / math.sqrt(n)), 2) if sd else 0.0,
            "pos_pct": round(100 * sum(1 for x in v if x > 0) / n, 1)}


def mdd(returns_pct):
    """누적 자본곡선의 최대 낙폭(%p). returns_pct 는 순차 실현 수익(%p)."""
    eq, peak, worst = 1.0, 1.0, 0.0
    for r in returns_pct:
        eq *= (1.0 + r / 100.0)
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1.0)
    return round(worst * 100.0, 3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--h5", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    rows, bad = load_rows(a.preds, a.tag)
    if not rows:
        print(f"[FAIL] 행 없음 tag={a.tag}", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in rows})
    dmin, dmax = min(r["date"] for r in rows), max(r["date"] for r in rows)
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * (a.h5 + 2))).date()
    conn = db_connect()
    series = price_series(conn, codes, d0, d1)

    # A 경로(익일 시가 청산)와 B 경로(5세션 뒤 종가 청산)를 같은 필터로 enrich
    ent, _ = enrich(rows, series, 1, "next_open")
    flt = SimpleNamespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                          min_price=None, max_price=None)
    ent, _ = apply_filters(ent, flt, on=True)
    long_kept, _ = enrich(rows, series, a.h5, "close_h")
    long_kept, _ = apply_filters(long_kept, flt, on=True)

    by_a, by_b = {}, {}
    for r in ent:
        by_a.setdefault(r["date"], []).append(r)
    for r in long_kept:
        by_b.setdefault(r["date"], []).append(r)

    dates_a, r1 = [], []
    for dt in sorted(by_a):
        rs = by_a[dt]
        if len(rs) < 20:
            continue
        top = sorted(rs, key=lambda r: -r["score"])[:a.k]
        dates_a.append(dt)
        r1.append(float(np.mean([r["gross_pct"] for r in top])) - RT)
    dates_b, r5 = [], []
    for dt in sorted(by_b):
        rs = by_b[dt]
        if len(rs) < 20:
            continue
        top = sorted(rs, key=lambda r: -r["score"])[:a.k]
        dates_b.append(dt)
        r5.append(float(np.mean([r["gross_pct"] for r in top])) - RT)
    if len(r1) < 30 or len(r5) < 30:
        print(f"[FAIL] 세션 부족 A={len(r1)} B={len(r5)}", file=sys.stderr)
        return 2

    # 비중첩 코호트(같은 간격)로 자본곡선 비교 — MDD 는 경로 의존이라 격자를 맞춰야 공정하다
    nb = len(r5) - (len(r5) % a.h5)
    r5_nov = [float(np.mean(r5[i:i + a.h5])) for i in range(0, nb, a.h5)]   # 코호트 내 세션 평균
    step = max(1, len(r1) // max(1, len(r5_nov)))
    r1_nov = [float(np.mean(r1[i * step:(i + 1) * step])) for i in range(len(r5_nov))]

    out = {
        "metric_name": "capital_path_compare",
        "tag": a.tag, "k": a.k, "h5": a.h5, "fee_roundtrip_pct": round(RT, 4),
        "note": "capital_session = A: mean(r1) · B: mean(r5)/h5 (자본을 1세션 묶어 둔 단위당 수익)",
        "path_a_h1": {"sessions": len(r1), **stat(r1), "capital_session": round(float(np.mean(r1)), 4),
                      "mdd_pct": mdd(r1)},
        "path_b_h5": {"sessions": len(r5), **stat(r5),
                      "capital_session": round(float(np.mean(r5)) / a.h5, 4),
                      "mdd_pct": mdd(r5)},
        "nonoverlap_compare": {
            "n_cohorts": len(r5_nov),
            "a_mdd_pct": mdd(r1_nov), "b_mdd_pct": mdd(r5_nov),
            "a_mean_pct": round(float(np.mean(r1_nov)), 4),
            "b_mean_pct": round(float(np.mean(r5_nov)), 4),
        },
        "window": {"first": dates_a[0], "last": dates_a[-1]},
        "rows_bad": bad,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"A(h1) 세션 {len(r1)} mean {out['path_a_h1']['mean']}%p cap/sess "
          f"{out['path_a_h1']['capital_session']} MDD {out['path_a_h1']['mdd_pct']} | "
          f"B(h5) 코호트 {len(r5)} mean {out['path_b_h5']['mean']}%p cap/sess "
          f"{out['path_b_h5']['capital_session']} MDD {out['path_b_h5']['mdd_pct']} | "
          f"비중첩 MDD A {out['nonoverlap_compare']['a_mdd_pct']} vs B "
          f"{out['nonoverlap_compare']['b_mdd_pct']}")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
