"""P&L 백테스트 시뮬레이터 유닛 테스트 (scripts/pnl_backtest.py)."""
import sys
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from pnl_backtest import apply_buy_cost, apply_sell_cost, compute_metrics, simulate


class TestCostModel:
    def test_buy_cost_increases_price(self):
        assert apply_buy_cost(10000) == pytest.approx(10000 * 1.00065)

    def test_sell_cost_decreases_price(self):
        assert apply_sell_cost(10000) == pytest.approx(10000 * (1 - 0.00015 - 0.0018 - 0.0005))

    def test_round_trip_is_negative(self):
        # 매수 후 즉시 매도하면 비용만큼 손실
        buy = apply_buy_cost(10000)
        sell = apply_sell_cost(10000)
        assert sell < buy


class TestMetrics:
    def test_flat_curve(self):
        m = compute_metrics(np.array([1e6, 1e6, 1e6]), [])
        assert m["total_return"] == 0.0
        assert m["max_drawdown"] == 0.0

    def test_uptrend_positive(self):
        m = compute_metrics(np.array([1e6, 1.1e6, 1.2e6, 1.3e6]), [0.1, 0.1])
        assert m["total_return"] == pytest.approx(0.3)
        assert m["win_rate"] == 1.0
        assert m["n_trades"] == 2

    def test_drawdown_detected(self):
        eq = np.array([1e6, 1.2e6, 0.9e6, 1.0e6])
        m = compute_metrics(eq, [0.2, -0.25, 0.111])
        assert m["max_drawdown"] < 0
        assert m["max_drawdown"] == pytest.approx(-0.25, abs=0.01)


def _make_df(dates, codes_prices, probs):
    """dates: [d1, d2, ...], codes_prices: {code: {date: price}}, probs: {code: prob}"""
    rows = []
    for code, prices in codes_prices.items():
        for d in dates:
            if d in prices:
                rows.append({"date": d, "stock_code": code, "price": prices[d],
                             "_prob": probs.get(code, 0.3)})
    return pd.DataFrame(rows)


class TestSimulate:
    def test_winner_long_position_profits(self):
        dates = [datetime(2026, 8, 3) + timedelta(days=i) for i in range(10)]
        prices = {"A": {d: 10000 + i * 100 for i, d in enumerate(dates)}}
        df = _make_df(dates, prices, {"A": 0.7})
        m = simulate(df, "date", "price", k=1, hold_days=3, threshold=0.6)
        assert m["n_trades"] >= 1
        assert m["total_return"] > 0

    def test_loser_long_position_losses(self):
        dates = [datetime(2026, 8, 3) + timedelta(days=i) for i in range(10)]
        prices = {"A": {d: 10000 - i * 100 for i, d in enumerate(dates)}}
        df = _make_df(dates, prices, {"A": 0.7})
        m = simulate(df, "date", "price", k=1, hold_days=3, threshold=0.6)
        assert m["total_return"] < 0

    def test_threshold_filters_low_prob(self):
        dates = [datetime(2026, 8, 3) + timedelta(days=i) for i in range(10)]
        prices = {"A": {d: 10000 + i * 100 for i, d in enumerate(dates)}}
        df = _make_df(dates, prices, {"A": 0.4})  # 임계 미달
        m = simulate(df, "date", "price", k=1, hold_days=3, threshold=0.6)
        assert m["n_trades"] == 0
        assert m["total_return"] == 0.0

    def test_no_trades_no_data(self):
        df = pd.DataFrame(columns=["date", "stock_code", "price", "_prob"])
        m = simulate(df, "date", "price", k=1, hold_days=3, threshold=0.6)
        assert m["n_trades"] == 0

    def test_holiday_gap_no_crash(self):
        """중간에 가격 없는 날(휴장일)이 있어도 equity가 0으로 폭락하면 안 됨.
        2026-08 실측: 5/25 휴장일에 포지션 전부 0 평가 → MDD -99%."""
        dates = [datetime(2026, 8, 3) + timedelta(days=i) for i in range(10)]
        gap_day = dates[5]
        prices = {"A": {d: 10000 + i * 100 for i, d in enumerate(dates) if d != gap_day}}
        df = _make_df(dates, prices, {"A": 0.7})
        m = simulate(df, "date", "price", k=1, hold_days=3, threshold=0.6)
        assert m["max_drawdown"] > -0.5  # -99% 폭락 없음
        assert m["total_return"] > 0
