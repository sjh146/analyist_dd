#!/usr/bin/env python3
"""피처 커버리지 게이트: 값이 사실상 없는 피처를 모델 입력에서 제외하고 리포트한다.

판정(최근 window 거래일 패널 기준):
  * NaN 비율 50% 초과 → drop_missing
  * 종목 간 상수(날짜별 표준편차 0) 비율 95% 초과 → drop_constant (시장공통 → 레짐 변수로만)
산출: reports/feature_gate.json {allowed, drop_missing, drop_constant, coverage}
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

PANEL = "/app/app/models/wf/panel_prod200_idn.npz"
OUT = "/app/reports/feature_gate.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=120)
    ap.add_argument("--panel", default=PANEL)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    d = np.load(args.panel, allow_pickle=True)
    X = np.asarray(d["X"], dtype=np.float32)
    names = [str(x) for x in d["feature_names"]]
    dates = np.array([str(x) for x in d["dates"]])
    keep = np.isin(dates, sorted(set(dates))[-args.window:])
    Xw, dw = X[keep], dates[keep]

    coverage = {}
    drop_missing, drop_constant = [], []
    for i, name in enumerate(names):
        col = Xw[:, i]
        cov = float(np.isfinite(col).mean())
        constant = 0.0
        ndays = 0
        for dt in sorted(set(dw)):
            vals = col[dw == dt]
            vals = vals[np.isfinite(vals)]
            if len(vals) < 5:
                continue
            ndays += 1
            if float(np.nanstd(vals)) == 0.0:
                constant += 1
        const_ratio = float(constant / ndays) if ndays else 1.0
        coverage[name] = {"nonzero": round(cov, 4), "constant_ratio": round(const_ratio, 4)}
        if cov < 0.5:
            drop_missing.append(name)
        elif const_ratio > 0.95:
            drop_constant.append(name)

    dropped = set(drop_missing) | set(drop_constant)
    result = {
        "window_days": args.window,
        "rows": int(Xw.shape[0]),
        "features": len(names),
        "allowed": [n for n in names if n not in dropped],
        "drop_missing": drop_missing,
        "drop_constant": drop_constant,
        "coverage": coverage,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(result, open(args.out, "w"), ensure_ascii=False, indent=1)
    print("features", len(names), "allowed", len(result["allowed"]),
          "drop_missing", len(drop_missing), "drop_constant", len(drop_constant))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
