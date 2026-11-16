#!/usr/bin/env python3
"""짝 비교 키 회귀 테스트 — fillable_topk_expectancy 의 pair_plan/by_date (CG318).

배경(실측 2026-11-17): arm 덤프 8폴드 · control 덤프 10폴드처럼 **폴드 구조가 다른**
두 파일을 (fold,date) 로 짝지으면 교집합이 8세션까지 줄고, 그 소표본 Δ 가 신호/종결
판정 근거로 쓰였다. 폴드 구조가 같을 때는 기존 동작이 비트 동일해야 한다.

실행: python3 scripts/_fillable_pair_key_test.py
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fillable_topk_expectancy as F  # noqa: E402


def _rows(folds, dates, n_codes=3):
    out = []
    for f in folds:
        for d in dates:
            for i in range(n_codes):
                out.append({"fold": f, "date": d, "code": f"{i:06d}", "score": 0.5,
                            "fwd_ret": 0.0})
    return out


def test_same_structure_uses_fold_date():
    a = _rows([1, 2], ["2026-01-02", "2026-01-05"])
    c = _rows([1, 2], ["2026-01-02", "2026-01-05"])
    mode, same = F.pair_plan(a, c)
    assert mode == "fold_date" and same is True, (mode, same)
    print("PASS same structure → (fold,date)")


def test_different_folds_uses_date():
    a = _rows([1, 2, 3, 4], ["2026-01-02", "2026-01-05"])
    c = _rows([1, 2], ["2026-01-05", "2026-01-06"])
    mode, same = F.pair_plan(a, c)
    assert mode == "date" and same is False, (mode, same)
    # (fold,date) 교집합은 2세션(폴드1·2 의 01-05), 날짜 기준은 1세션(01-05).
    legacy = F.paired_stats({(1, "2026-01-05"): .1, (2, "2026-01-05"): .1},
                            {(1, "2026-01-05"): .0, (2, "2026-01-05"): .0})
    assert legacy["n_dates"] == 2
    fixed = F.paired_stats(F.by_date({(1, "2026-01-05"): .1, (2, "2026-01-05"): .1}),
                           F.by_date({(1, "2026-01-05"): .0, (2, "2026-01-05"): .0}))
    assert fixed["n_dates"] == 1, fixed
    print("PASS different folds → date 짝 (중복 세션 접힘)")


def test_by_date_averages_folds():
    got = F.by_date({(1, "d1"): 1.0, (2, "d1"): 3.0, (1, "d2"): 2.0})
    assert got == {"d1": 2.0, "d2": 2.0}, got
    print("PASS by_date 평균 집계")


def test_identical_dumps_zero_delta():
    """같은 파일끼리 짝 → Δ 0 이어야 한다(짝짓기 자기검정)."""
    b = F.baskets([{**r, "gross_pct": 1.0} for r in _rows([1, 2], ["d1", "d2"])],
                  [1, 2], 0.21)
    p = F.paired_stats(b[1], b[1])
    assert p["n_dates"] == 4 and abs(p["delta_mean"]) < 1e-12, p  # (2폴드 × 2날짜)
    print("PASS 자기짝 Δ=0")


if __name__ == "__main__":
    test_same_structure_uses_fold_date()
    test_different_folds_uses_date()
    test_by_date_averages_folds()
    test_identical_dumps_zero_delta()
    print("ALL PASS")
