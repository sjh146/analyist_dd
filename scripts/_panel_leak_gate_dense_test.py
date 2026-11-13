#!/usr/bin/env python3
"""panel_leak_gate 의 --dense-only 모드 회귀 테스트 (2026-11-13).

검증하는 계약:
  A. legacy(기본) 동작은 종전과 동일하다 — 조밀·고정 컬럼은 LEAKY.
  B. legacy 는 '희소하지만 시간가변'인 컬럼(0 대체가 절반 이상)을 **오탐**한다(수리 대상 증명).
  C. --dense-only 는 희소 컬럼을 판정 제외(DQ 소관)로 돌려 B 의 오탐을 없앤다.
  D. --dense-only 도 조밀·고정 컬럼은 LEAKY 로 잡는다(누수 검출력 유지).
  E. --dense-only 는 조밀·시간가변 컬럼을 CLEAN 으로 둔다.
실측 근거: panel_200liq_fresh 의 value_pcr(비영 44.4%·비영 중 240+ 고유) vs
          panel_420_asofpatch 의 value_pbr(비영 96%·종목당유니크 1).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel_leak_gate as g  # noqa: E402

DATE = "2026-01-01"


def _panel(n_codes=50, n_days=40):
    """codes/dates/value_pbr/value_per/value_pcr 5컬럼짜리 합성 패널."""
    codes, vals = [], []
    rng = np.random.default_rng(7)
    for c in range(n_codes):
        for d in range(n_days):
            codes.append(f"{c:06d}")
            vals.append([rng.random(), rng.random(), rng.random()])
    X = np.array(vals, dtype=np.float64)
    names = ["value_pbr", "value_per", "value_pcr"]
    return names, X, codes, [DATE] * len(codes)


def _set_dense_frozen(names, X, codes, col, n_codes=50):
    """조밀(전 행 비영) + 종목별 고정 — 진짜 누수 지문."""
    j = names.index(col)
    for c in range(n_codes):
        idx = [i for i, cc in enumerate(codes) if cc == f"{c:06d}"]
        X[idx, j] = 1.0 + c  # 종목마다 다른 값이지만 시간 불변


def _set_sparse_varying(names, X, codes, col, n_codes=50, keep=18):
    """희소(일부 종목만 비영) + 비영 종목 안에서는 시간가변 — 커버리지 결함이지 누수 아님."""
    j = names.index(col)
    for c in range(n_codes):
        idx = [i for i, cc in enumerate(codes) if cc == f"{c:06d}"]
        if c < keep:
            for k, i in enumerate(idx):
                X[i, j] = 10.0 + k  # 일별 변동
        else:
            X[idx, j] = 0.0


def _run(dense_only, setup):
    names, X, codes, dates = _panel()
    setup(names, X, codes)
    r = g.classify(names, X, codes, dates, dense_only=dense_only)
    return r


def t_legacy_dense_frozen_leaky():
    r = _run(False, lambda n, X, c: _set_dense_frozen(n, X, c, "value_pbr"))
    assert r["verdict"] == "LEAKY", r
    assert "value_pbr" in r["leaky_columns"], r["leaky_columns"]


def t_legacy_sparse_varying_false_positive():
    r = _run(False, lambda n, X, c: _set_sparse_varying(n, X, c, "value_pcr"))
    # 종전 규칙은 이것을 LEAKY 로 본다 — 이 오탐이 수리 대상이다.
    assert r["verdict"] == "LEAKY", r
    assert "value_pcr" in r["leaky_columns"], r["leaky_columns"]


def t_dense_only_sparse_varying_clean():
    r = _run(True, lambda n, X, c: _set_sparse_varying(n, X, c, "value_pcr"))
    assert r["verdict"] == "CLEAN", r
    assert "value_pcr" in r["sparse_columns"], r["sparse_columns"]
    assert r["mode"] == "dense_only"


def t_dense_only_dense_frozen_leaky():
    r = _run(True, lambda n, X, c: _set_dense_frozen(n, X, c, "value_pbr"))
    assert r["verdict"] == "LEAKY", r
    assert "value_pbr" in r["leaky_columns"], r["leaky_columns"]


def t_dense_only_dense_varying_clean():
    def setup(names, X, codes):
        j = names.index("value_per")
        for k in range(len(codes)):
            X[k, j] = 1.0 + k  # 전 행 비영 · 일별 변동

    r = _run(True, setup)
    assert r["verdict"] == "CLEAN", r


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(tests) - fails}/{len(tests)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
