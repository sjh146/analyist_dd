#!/usr/bin/env python3
"""패널의 **재무비율 컬럼만** 실제 공시 접수일(rcept_dt) 기준 as-of 값으로 교체한다.

WHY (2026-10-05, XR1/R12)
- `app/feature_engine/factor_features.asof_report_predicate()` 는 `financial_statements.report_date`
  (기간 **말일**) 에 **보수적 지연**(연간 90일 · 그 밖 45일)을 더해 가시 시점을 추정한다
  (rcept_dt 컬럼이 `financial_statements` 에 없기 때문).
- 그런데 DB 실측(2026-10-05): `disclosures`·`financial_ratio_features` 에 실제 접수일이 있다
  — 종목×기간쌍 7,242개 중 96.9% · 연간 중위 지연 **77일**(90일 가정보다 13일 보수적),
  지연 초과(가정보다 늦게 접수 = **루킹어헤드**) 연간 0.92% · 반기 1.19%.
  즉 현 패널 재무컬럼은 ① 연간 보고서를 최대 13일 늦게 반영하고 ② 소수지만 미래정보를 쓴다.
- 리서처가 만든 `financial_ratio_features`(R12) 격자는 **rcept_dt <= 거래일** 로 이미 정확히
  구성돼 있다(316거래일 · 2025-06-16~2026-09-29). 전체 재빌드(수 시간) 대신 컬럼만 교체한다
  (patch_panel_asof / patch_panel_disclosure 와 같은 논리 — 같은 행 위 A/B 라 통제가 깨끗하다).

검증(교체가 실제로 일어났는지): 컬럼별 ① 비영 비율 before/after ② 값이 바뀐 셀 비율
③ 중위값 비율(스케일 정합 sanity) 을 출력한다. 그리고 교체에 쓰인 값의 rcept_dt 가 거래일보다
미래인 셀이 0 인지 확인한다(빌더가 보장하지만 사후 증명을 남긴다).

사용 (xgboost-ml 컨테이너, cwd=/app):
    docker exec stock_xgboost_ml python /app/scripts/patch_panel_fin_rcept.py \
        --in app/models/wf/panel_prod200.npz --out app/models/wf/panel_prod200_rcept.npz
"""
from __future__ import annotations

import argparse
import json
import os
import statistics

import numpy as np
import psycopg2

# financial_ratio_features 의 피처 컬럼(스키마/키 컬럼 제외)
GRID_COLS = [
    "value_per", "value_pbr", "value_psr", "value_pcr", "value_ncav",
    "value_ev_ebit", "value_pfcr",
    "quality_cp_to_assets", "quality_op_to_equity", "quality_roe", "quality_roa",
    "quality_f_score", "quality_asset_growth", "quality_debt_ratio_change",
    "quality_op_growth", "quality_earnings_volatility",
    "roe", "per_current", "pbr_current",
]


def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="패널 재무비율 컬럼 rcept_dt as-of 재계산")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--limit", type=int, default=0, help="앞 N행만 처리(스모크, 0=전체)")
    ap.add_argument("--cols", default="", help="쉼표 구분 컬럼 목록(기본: 패널∩GRID_COLS 전체)")
    ap.add_argument("--report", default="", help="선택: 컬럼별 검증 결과를 이 경로에 JSON 으로 쓴다")
    args = ap.parse_args()

    z = np.load(args.src, allow_pickle=True)
    X = z["X"].astype(np.float64, copy=True)
    names = [str(n) for n in z["feature_names"]]
    dates = [str(d)[:10] for d in z["dates"]]
    codes = [str(c) for c in z["codes"]]

    if args.cols:
        want = [c.strip() for c in args.cols.split(",") if c.strip()]
    else:
        want = [c for c in GRID_COLS if c in names]
    missing = [c for c in (args.cols.split(",") if args.cols else []) if c.strip() and c.strip() not in names]
    if missing:
        print(f"STOP: 패널에 없는 컬럼 {missing}")
        return 1
    if not want:
        print("STOP: 교체할 컬럼이 없음(패널 ∩ GRID_COLS = 공집합)")
        return 1

    idx = {c: names.index(c) for c in want}
    uniq_codes = sorted(set(codes))
    dmin, dmax = min(dates), max(dates)

    conn = _pg_connect()
    cur = conn.cursor()
    ph = "(" + ",".join(["%s"] * len(uniq_codes)) + ")"
    sel = ", ".join(["stock_code", "trade_date::text", "rcept_dt::text"] + want)
    cur.execute(
        f"SELECT {sel} FROM financial_ratio_features "
        f"WHERE stock_code IN {ph} AND trade_date BETWEEN %s AND %s",
        uniq_codes + [dmin, dmax],
    )
    grid: dict[tuple[str, str], tuple[list[float], str]] = {}
    for row in cur.fetchall():
        code, tdate, rcept = str(row[0]), str(row[1])[:10], row[2]
        vals = [(float(v) if v is not None else 0.0) for v in row[3:]]
        grid[(code, tdate)] = (vals, (str(rcept)[:10] if rcept else ""))
    conn.close()

    n = len(codes) if not args.limit else min(args.limit, len(codes))
    per_col = {c: {"before_nz": int(np.count_nonzero(X[:n, idx[c]]))} for c in want}
    hit = 0
    changed = {c: 0 for c in want}
    future_vis = 0
    before_med = {c: float(np.median(X[:n, idx[c]])) for c in want}

    for i in range(n):
        g = grid.get((codes[i], dates[i]))
        if g is None:
            continue
        hit += 1
        vals, rcept = g
        if rcept and rcept > dates[i]:
            future_vis += 1
        for j, c in enumerate(want):
            old = X[i, idx[c]]
            new = vals[j]
            if new != old:
                changed[c] += 1
            X[i, idx[c]] = new

    rep = {
        "panel_in": args.src, "panel_out": args.dst,
        "rows_processed": n, "grid_hit": hit, "grid_hit_pct": round(100.0 * hit / max(n, 1), 2),
        "grid_rows_loaded": len(grid), "date_range": [dmin, dmax],
        "future_rcept_cells": future_vis,   # >0 이면 이 패치 자체가 누수 → 중단 대상
        "cols": {},
    }
    print(f"패널 {n}행 · 격자 적중 {hit} ({rep['grid_hit_pct']}%) · 미래 rcept 셀 {future_vis}")
    for c in want:
        after_nz = int(np.count_nonzero(X[:n, idx[c]]))
        after_med = float(np.median(X[:n, idx[c]]))
        ratio = (after_med / before_med[c]) if before_med[c] else None
        rep["cols"][c] = {
            "nz_before_pct": round(100.0 * per_col[c]["before_nz"] / max(n, 1), 2),
            "nz_after_pct": round(100.0 * after_nz / max(n, 1), 2),
            "changed_pct": round(100.0 * changed[c] / max(n, 1), 2),
            "median_before": before_med[c], "median_after": after_med,
            "median_ratio": (round(ratio, 3) if ratio is not None else None),
        }
        print(f"  {c:30s} nz {rep['cols'][c]['nz_before_pct']:5.2f}%→{rep['cols'][c]['nz_after_pct']:5.2f}%  "
              f"changed {rep['cols'][c]['changed_pct']:5.2f}%  med {before_med[c]:+.4g}→{after_med:+.4g}")

    if future_vis:
        print("STOP: 격자에 미래 접수일(rcept_dt > 거래일) 셀이 있다 — 패치 중단(교체 저장 안 함)")
        return 1

    out = dict(z)
    out["X"] = X.astype(z["X"].dtype, copy=False) if z["X"].dtype != np.float64 else X
    os.makedirs(os.path.dirname(args.dst) or ".", exist_ok=True)
    np.savez_compressed(args.dst, **out)
    print(f"저장: {args.dst}")

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print(f"검증 리포트: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
