#!/usr/bin/env python3
"""CG460 회귀 — panel_feature_screen 의 커버리지 인지 분류(0.0 결측 채움 오분류 차단).

실측 배경(2026-11-24 XR49/CG460): 무정보 126/210 중 상당수가 '시장레벨 41' 로 분류됐는데,
패널은 결측을 0.0 으로 채우므로 값이 거의 없는 열도 '날짜내 유니크 1' 이 90% 이상이 되어
시장레벨로 자동 분류된다 → 죽은 피처 수가 실제와 다르게 보고되고, 데이터 축 판단이 흔들린다.

이 테스트는 classify_column 이 (1) 0.0 결측 열을 시장레벨로 부르지 않고 coverage 로 보내며
(2) 진짜 시장레벨(날짜별 상수·날짜간 변동)은 계속 시장레벨로 유지하고
(3) 종목상수 재무 열은 종목상수로 유지하는지 확인한다.

실행(로컬 또는 컨테이너):
  python3 scripts/_cg460_screen_class_test.py
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app/scripts")
sys.path.insert(0, "scripts")
sys.path.insert(0, ".")

from panel_feature_screen import classify_column  # noqa: E402


def _grid(n_codes=12, n_dates=20, seed=0):
    rng = np.random.default_rng(seed)
    codes = np.array([f"{i:06d}" for i in range(n_codes) for _ in range(n_dates)])
    dates = np.array([f"2026-01-{d+1:02d}" for _ in range(n_codes) for d in range(n_dates)])
    return rng, codes, dates


def case_sparse_zero_fill():
    """0.0 채움 희소 열: 20날짜 중 2날짜만 실제 값 → 시장레벨 아님·coverage."""
    _, codes, dates = _grid()
    col = pd.Series(np.zeros(len(codes)))
    col.iloc[0] = 0.5
    rec = classify_column(col, codes, dates)
    ok = (rec["market_level"] is False) and (rec["coverage_gap"] is True)
    return ("0.0 채움 희소 열 → coverage(시장레벨 아님)", ok,
            f"market_level={rec['market_level']} coverage_gap={rec['coverage_gap']} "
            f"nonzero={rec['nonzero_ratio']} per_date_const={rec['per_date_constant_ratio']} "
            f"cross_date_uniques={rec['cross_date_uniques']}")


def case_true_market_level():
    """진짜 시장레벨: 날짜마다 종목 공통 상수이고 그 값이 날짜마다 변한다."""
    rng, codes, dates = _grid()
    dvals = {d: float(rng.normal()) for d in sorted(set(dates))}
    col = pd.Series([dvals[d] for d in dates])
    rec = classify_column(col, codes, dates)
    ok = (rec["market_level"] is True) and (rec["coverage_gap"] is False)
    return ("진짜 시장레벨(날짜 상수·날짜간 변동) → market_level", ok,
            f"market_level={rec['market_level']} cross_date_uniques={rec['cross_date_uniques']} "
            f"per_date_const={rec['per_date_constant_ratio']}")


def case_stock_constant():
    """종목상수 재무 열: 종목마다 값 고정, 날짜에 따라 안 변함."""
    rng, codes, dates = _grid()
    per_code = {c: float(rng.normal()) for c in sorted(set(codes))}
    col = pd.Series([per_code[c] for c in codes])
    rec = classify_column(col, codes, dates)
    ok = (rec["stock_constant"] is True) and (rec["coverage_gap"] is False)
    return ("종목상수 열 → stock_constant(커버리지 정상)", ok,
            f"stock_constant={rec['stock_constant']} coverage_gap={rec['coverage_gap']}")


def case_normal_feature():
    """정상 시간가변 피처: 어느 버킷에도 안 걸린다."""
    rng, codes, dates = _grid()
    col = pd.Series(rng.normal(size=len(codes)))
    rec = classify_column(col, codes, dates)
    ok = (not rec["market_level"]) and (not rec["stock_constant"]) and (not rec["coverage_gap"])
    return ("정상 시간가변 피처 → 버킷 없음", ok,
            f"market_level={rec['market_level']} stock_constant={rec['stock_constant']} "
            f"coverage_gap={rec['coverage_gap']}")


def case_all_nan():
    """전부 결측 → coverage·시장레벨 아님(예외 없이)."""
    _, codes, dates = _grid()
    col = pd.Series(np.full(len(codes), np.nan))
    rec = classify_column(col, codes, dates)
    ok = (rec["coverage_gap"] is True) and (rec["market_level"] is False) and rec["n_obs"] == 0
    return ("전부 결측 → coverage", ok, f"n_obs={rec['n_obs']} coverage_gap={rec['coverage_gap']}")


def main():
    cases = [case_sparse_zero_fill(), case_true_market_level(), case_stock_constant(),
             case_normal_feature(), case_all_nan()]
    npass = 0
    for name, ok, detail in cases:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        npass += int(ok)
    print(f"\n{npass}/{len(cases)} PASS")
    return 0 if npass == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
