#!/usr/bin/env python3
"""_cg278_period_split.py — CG278 후속: top-k 초과기대(Δ top-k − 세션 풀평균)를 **분기별**로 분해.

WHY(2026-11-14 CG278 실측): 배포가능 arm(CO_smooth_d1_h5)의 h5 k=3 Δ 는 +1.703%p/세션(t 3.50)인데
앞/뒤 절반이 +3.36 / −0.08 로 갈렸고 폴드 승률도 fold1 38/45 vs fold2 16/45 로 한쪽에 몰렸다.
즉 '평균은 유의하지만 시간적으로 재현되지 않는다' → 어느 구간이 캐리하는지 분기 단위로 본다.
새 학습 0(같은 덤프 재사용) · 읽기 전용.

사용(컨테이너): python scripts/_cg278_period_split.py --jsonl /app/reports/overnight/cg278_preds_allcodes.jsonl \
  --tag CO_smooth_d1_h5 --k 3 --horizon 5 --json-out /app/reports/overnight/cg278_period_split.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fillable_topk_expectancy as fte   # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--json-out", required=True)
    a = ap.parse_args(argv)

    rows, bad = fte.load_rows(a.jsonl, a.tag)
    if not rows:
        print("[FAIL] rows 0", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in rows})
    dmin = min(r["date"] for r in rows)
    dmax = max(r["date"] for r in rows)
    import pandas as pd
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * (a.horizon + 2))).date()
    conn = fte.db_connect()
    series = fte.price_series(conn, codes, d0, d1)
    er, _ = fte.enrich(rows, series, a.horizon, "close_h")

    # 체결성 필터(기본값과 동일) — apply_filters 는 argparse 객체를 받는다.
    ns = argparse.Namespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                            min_price=None, max_price=None)
    fa, _ = fte.apply_filters(er, ns, True)
    rt = (fte.FEE_BUY + fte.FEE_SELL + fte.TAX_SELL) * 100.0
    ba = fte.baskets(fa, [a.k], rt)[a.k]
    pool = fte.pool_series(fa, rt)

    per = defaultdict(list)
    for key in ba:
        if key in pool:
            per[key[1][:7]].append(ba[key] - pool[key])   # 'YYYY-MM'
    out = {"tag": a.tag, "k": a.k, "horizon": a.horizon, "fee_roundtrip_pct": rt,
           "by_month": {}, "by_halves": {}}
    for m in sorted(per):
        v = np.asarray(per[m], dtype=float)
        out["by_month"][m] = {"n_sessions": int(len(v)), "delta_mean": float(v.mean()),
                              "t": float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))
                              if len(v) > 1 and v.std(ddof=1) > 0 else None,
                              "pos_pct": float((v > 0).mean() * 100)}
    ms = sorted(per)
    mid = len(ms) // 2
    for name, chunk in (("front", ms[:mid]), ("back", ms[mid:])):
        v = np.asarray([x for m in chunk for x in per[m]], dtype=float)
        out["by_halves"][name] = {"months": chunk, "n_sessions": int(len(v)),
                                  "delta_mean": float(v.mean()) if len(v) else None}
    os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[ok] {a.json_out}")
    for m in ms:
        r = out["by_month"][m]
        print(f"  {m}: n={r['n_sessions']:>3} Δ {r['delta_mean']:+.3f}%p "
              f"(t {r['t'] if r['t'] is None else round(r['t'], 2)}) 양세션 {r['pos_pct']:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
