#!/usr/bin/env python3
"""패널 재무 계열 컬럼의 **종목당 유니크값 개수** 실측 (as-of 누수 진단).

WHY: `FactorFeatures.get_all_factors` / `QualityScorer.get_f_score` 는 date 를 받지 않아
빌드 시점의 **최신 재무 스냅샷**을 모든 과거 행에 쓴다 → 그 컬럼들은 종목 내 상수(유니크 1)가
된다. as-of 가 제대로 적용되면 보고서가 갱신되는 만큼 유니크값이 2~4개 나와야 한다.
(선례: company_features 컬럼은 patch_panel_asof 로 1 → 3 으로 교체된 실측이 있다.)

사용: docker exec stock_xgboost_ml python /app/scripts/_panel_leak_probe.py
"""
from __future__ import annotations

import collections
import os

import numpy as np

BASE = "/app/app/models/wf/"
TARGETS = [
    "quality_roa", "quality_score", "quality_f_score", "quality_asset_growth",
    "value_per", "value_pbr", "value_psr", "value_pcr", "value_ncav",
    "roe", "per_current", "pbr_current", "debt_ratio", "op_margin", "net_margin",
    "revenue", "operating_profit", "net_income",
]


def probe(fn: str) -> None:
    p = BASE + fn
    if not os.path.exists(p):
        print(f"{fn}: MISSING")
        return
    z = np.load(p, allow_pickle=True)
    names = [str(n) for n in z["feature_names"]]
    X = z["X"]
    codes = [str(c) for c in z["codes"]]
    dates = [str(d)[:10] for d in z["dates"]]
    print(f"== {fn} rows={X.shape[0]} cols={len(names)} dates={min(dates)}~{max(dates)}")
    for i, n in enumerate(names):
        if n not in TARGETS:
            continue
        per = collections.defaultdict(set)
        for r, c in enumerate(codes):
            per[c].add(round(float(X[r, i]), 6))
        uniq = sorted(len(v) for v in per.values())
        nz = 100.0 * float(np.mean(X[:, i] != 0))
        print(f"   {n:20s} 종목수 {len(per):4d} 종목당유니크 중앙 {uniq[len(uniq)//2]:3d} "
              f"min {uniq[0]} max {uniq[-1]} | 비영 {nz:6.2f}%")


if __name__ == "__main__":
    for f in ("panel_420_asofpatch.npz", "panel_420.npz", "panel_150u.npz", "panel_995.npz"):
        probe(f)
