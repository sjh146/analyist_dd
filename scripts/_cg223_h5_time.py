#!/usr/bin/env python3
"""CG223 — h5 초과수익(체결 가능 표본)의 **시간 분해**: 지금 배포하면 돈이 되는가.

왜 (2026-11-13 02:0x 엔지니어 자율 틱)
  · CG220: h5 보유 시 비중첩 Δ+2.2966%p(t 3.72, n48) — 5시드·8폴드에서 유지.
  · CG221: 변동성 매칭 널 Δ+1.921(비율 1.098) — 변동성 베팅 아님(교란 기각).
  · CG222: 체결성 필터 적용 시 top5 +1.500%p/5세션 vs 풀 +0.290 → Δ+1.2102(t 3.54) — 필터 후에도 유지.
  그런데 CG222 의 **분할표본**이 앞 +2.526 / 뒤 +0.474 였다 → 이득의 대부분이 과거 절반에 몰려 있다.
  '과거에 있었다'와 '지금 배포하면 돈이 된다'는 다르다. 최근 구간에서 같은 Δ 가 남는지 본다.

사전 등록(성공/실패 판정):
  최근 60세션 Δ ≥ +0.1%p/5세션 & t ≥ 2  → 시간 강건(배포 검토 근거로 올림)
  최근 60세션 Δ ≤ 0 또는 t < 1          → 시간 감쇠 — h5 축의 **배포 근거 아님**(축은 연구로만 유지)
  그 사이                               → 판정불가(표본 부족)

읽기 전용·학습 0회. 체결성 필터(상한가·일변동 25%·거래대금 10억)는 fillable_topk_expectancy 와 동일.
재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && timeout 900 python -u scripts/_cg223_h5_time.py \
     --preds /app/reports/overnight/cg220_preds.jsonl --tag CO_smooth_d1_h5 --k 5 --horizon 5 \
     --out /app/reports/overnight/cg223_time.json'
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
from fillable_topk_expectancy import (  # noqa: E402
    apply_filters, db_connect, enrich, load_rows, price_series,
)


def st(v):
    v = [x for x in v if x is not None]
    n = len(v)
    if n < 2:
        return {"n": n}
    m = sum(v) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))
    return {"n": n, "mean": round(m, 4), "sd": round(sd, 4),
            "t": round(m / (sd / math.sqrt(n)), 2) if sd else 0.0,
            "pos_pct": round(100 * sum(1 for x in v if x > 0) / n, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--recent-sessions", type=int, default=60)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    rows, bad = load_rows(a.preds, a.tag)
    if not rows:
        print(f"[FAIL] 행 없음 tag={a.tag}", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in rows})
    dmin = min(r["date"] for r in rows)
    dmax = max(r["date"] for r in rows)
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * (a.horizon + 2))).date()
    conn = db_connect()
    series = price_series(conn, codes, d0, d1)
    kept, _ = enrich(rows, series, a.horizon, "close_h")
    flt = SimpleNamespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                          min_price=None, max_price=None)
    kept, dropped = apply_filters(kept, flt, on=True)

    by_date = {}
    for r in kept:
        by_date.setdefault(r["date"], []).append(r)
    deltas, dates = [], []
    for dt in sorted(by_date):
        rs = by_date[dt]
        if len(rs) < 20:
            continue
        pool = float(np.mean([r["gross_pct"] for r in rs]))
        top = sorted(rs, key=lambda r: -r["score"])[:a.k]
        basket = float(np.mean([r["gross_pct"] for r in top]))
        deltas.append(basket - pool)
        dates.append(dt)
    if len(deltas) < 30:
        print(f"[FAIL] 세션 부족 n={len(deltas)}", file=sys.stderr)
        return 2

    n = len(deltas)
    half = n // 2
    per_q = max(1, n // 4)
    quarters = {}
    for q in range(4):
        lo, hi = q * per_q, (n if q == 3 else (q + 1) * per_q)
        if hi - lo >= 5:
            quarters[f"q{q + 1}_{dates[lo]}~{dates[hi - 1]}"] = st(deltas[lo:hi])
    recent = deltas[-a.recent_sessions:]
    nov = deltas[::a.horizon]          # 비중첩 진입(h 간격)
    out = {
        "metric_name": "h5_time_profile",
        "tag": a.tag, "k": a.k, "horizon": a.horizon,
        "fee_note": "수수료 왕복은 gross_pct 계산에 이미 반영(fillable_topk_expectancy 동일 경로)",
        "n_sessions": n, "rows_kept": len(kept), "rows_dropped_price": dropped,
        "window": {"first": dates[0], "last": dates[-1]},
        "overall": st(deltas),
        "halves": {"front": st(deltas[:half]), "back": st(deltas[half:])},
        "quarters": quarters,
        "recent": {"sessions": len(recent), "from": dates[-len(recent)], **st(recent)},
        "nonoverlap": st(nov),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"세션 {n} ({dates[0]}~{dates[-1]}) 전체 Δ{out['overall']['mean']}"
          f"(t {out['overall']['t']}) · 앞 {out['halves']['front']['mean']}"
          f" / 뒤 {out['halves']['back']['mean']} · 최근{len(recent)} Δ{out['recent']['mean']}"
          f"(t {out['recent']['t']}) · 비중첩(n{len(nov)}) Δ{out['nonoverlap']['mean']}"
          f"(t {out['nonoverlap']['t']})")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
