"""tests/test_rank_band.py — 상승률 대역 사용 방식(사용자 선택 2번) 검증.

docs/spec_rank_universe.md §2(개정):
  · excluded_codes(추격 대역, 기본 +12~30%) → 유니버스에서 **제외**
  · codes(2~12%) → **정렬 가점**(공개 score 값은 불변)

여기서는 DB 없이 ``FixtureProvider`` 로 파이프라인 계약만 검증한다:
① 추격 배제가 실제로 후보에서 빠진다 ② 가점은 **순위만** 바꾸고 score 는 그대로
③ 가점 0 이면 기존 순위와 동일 ④ 빈 집합/None 이면 기존 동작과 완전 동일
"""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from day_trading_engine import FixtureProvider, run_screener  # noqa: E402


def _frame(code, closes, vols, values=None, start=date(2026, 7, 1)):
    n = len(closes)
    if values is None:
        values = [1e9] * n
    return pd.DataFrame({
        "stock_code": [code] * n,
        "trade_date": pd.date_range(start, periods=n, freq="D"),
        "open_price": closes, "high_price": np.asarray(closes) * 1.01,
        "low_price": np.asarray(closes) * 0.99, "close_price": closes,
        "volume": vols, "trading_value": values,
    })


def _provider():
    # A: 완만한 상승(점수 중간) · B: 강한 상승(점수 최고) · C: 하락 추세(점수 낮음)
    a = _frame("000001", np.linspace(5000, 5600, 25), np.linspace(1e6, 1.2e6, 25))
    b = _frame("000002", np.linspace(3000, 6000, 25), np.linspace(1e6, 3e6, 25))
    c = _frame("000003", np.linspace(6000, 4000, 25), np.full(25, 1e6))
    return FixtureProvider(pd.concat([a, b, c], ignore_index=True),
                           signal_date="2026-07-25")


def _run(**kw):
    return run_screener(_provider(), top_n=10, lookback=20, min_history=10,
                        min_price=1000, **kw)


def test_baseline_ranks_without_bands():
    base = _run()
    # 실측 기준선: 000001(36.8) > 000002(23.5) > 000003(21.4)
    assert list(base["stock_code"])[0] == "000001"


def test_exclude_codes_removes_candidate():
    out = _run(exclude_codes={"000002"})
    assert "000002" not in set(out["stock_code"])


def test_bonus_changes_order_but_not_score():
    base = _run()
    scored = dict(zip(base["stock_code"], base["score"]))
    out = _run(bonus_codes={"000003"}, bonus_points=100.0)
    # 점수 값은 그대로(정렬 전용 가점)
    for code, score in zip(out["stock_code"], out["score"]):
        assert score == pytest.approx(scored[code])
    assert list(out["stock_code"])[0] == "000003"        # 가점으로 1위로 올라옴


def test_zero_bonus_keeps_baseline_order():
    base = list(_run()["stock_code"])
    same = list(_run(bonus_codes={"000003"}, bonus_points=0.0)["stock_code"])
    assert base == same


def test_empty_bands_are_noop():
    base = list(_run()["stock_code"])
    same = list(_run(exclude_codes=set(), bonus_codes=set(), bonus_points=5.0)["stock_code"])
    assert base == same


def test_rank_score_column_not_leaked():
    out = _run(bonus_codes={"000003"}, bonus_points=100.0)
    assert "_rank_score" not in out.columns
