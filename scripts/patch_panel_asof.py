#!/usr/bin/env python3
"""기존 패널의 **재무 피처 컬럼만** as-of 규칙으로 교체한다 (전체 재빌드 대체).

WHY (2026-09-25 실측)
- 누수는 `company_features.get_financial_features` 가 `date` 를 받지 않아 **항상 최신 재무
  스냅샷**을 쓴 데서 발생했다(2025-08 행에 2026-06 보고서 → 최대 10개월 룩어헤드).
- 전체 패널 재빌드는 50종목×281일=13,609 페어에 85분+ 걸리고, 그 사이 컨테이너가 재시작하면
  (04:00:55 Exit=0 재시작 실측) 진행분이 전부 소실된다.
- 누수는 재무 계열 10여 개 컬럼에서만 발생하므로 **그 컬럼만** 다시 계산하면:
  ① 수 분 내 완료 ② 같은 패널·같은 행·같은 다른 피처 위에서 A/B → 통제가 더 깨끗하다.

사용 (xgboost-ml 컨테이너, cwd=/app):
    docker exec stock_xgboost_ml python scripts/patch_panel_asof.py \
        --in app/models/wf/panel_420.npz --out app/models/wf/panel_420_asofpatch.npz
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, "/app")

import psycopg2  # noqa: E402


def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="패널 재무 컬럼 as-of 교체")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--every", type=int, default=1500, help="진행 로그 주기(행)")
    args = ap.parse_args()

    z = np.load(args.src, allow_pickle=True)
    X = z["X"].astype(np.float64, copy=True)
    names = [str(n) for n in z["feature_names"]]
    dates = [str(d)[:10] for d in z["dates"]]
    codes = [str(c) for c in z["codes"]]
    price = z["price"]
    idx = {n: i for i, n in enumerate(names)}

    from app.feature_engine.company_features import CompanyFeatures

    conn = _pg_connect()
    cf = CompanyFeatures()
    # 기준선: 교체 전 컬럼별 종목당 유니크값 개수 (룩어헤드 = 종목 상수)
    def stock_const_counts(arr):
        out = {}
        for j, name in enumerate(names):
            per = {}
            for i, c in enumerate(codes):
                per.setdefault(c, set()).add(round(float(arr[i, j]), 6))
            vals = [len(v) for v in per.values()]
            vals.sort()
            out[name] = vals[len(vals) // 2] if vals else 0
        return out
    before_const = stock_const_counts(X)
    before = X.copy()

    touched = Counter()
    changed_cells = 0
    for i in range(X.shape[0]):
        feats = cf.get_financial_features(codes[i], conn, date=dates[i])
        for k, v in feats.items():
            j = idx.get(k)
            if j is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(fv):
                continue
            if X[i, j] != fv:
                changed_cells += 1
                touched[k] += 1
            X[i, j] = fv
        if (i + 1) % args.every == 0:
            print(f"  진행 {i + 1}/{X.shape[0]} 변경셀 {changed_cells}", flush=True)
    conn.close()

    after_const = stock_const_counts(X)
    print(f"\n[as-of 패치] 변경된 셀 {changed_cells} / 교체 컬럼 {len(touched)}개")
    for k, n in touched.most_common():
        print(f"    {k:24s} {n:6d}행 변경 | 종목당 유니크(중앙) {before_const[k]} → {after_const[k]}")

    np.savez_compressed(args.dst, X=X.astype(np.float32), feature_names=z["feature_names"],
                        dates=z["dates"], codes=z["codes"], price=price)
    print(f"[as-of 패치] 저장: {args.dst} ({X.shape[0]}행 × {X.shape[1]}피처)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
