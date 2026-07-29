"""
Tests for PositionSizer extension methods (kelly, var, volatility position).
"""

import pytest
from app.risk_management.position_sizer import PositionSizer


class TestPositionSizerKelly:
    def test_kelly_fraction_normal(self):
        sizer = PositionSizer()
        kelly = sizer.calculate_kelly_fraction(0.6, 2.0, 1.0)
        assert 0.0 < kelly <= 0.25

    def test_kelly_fraction_clamped(self):
        sizer = PositionSizer()
        kelly = sizer.calculate_kelly_fraction(0.9, 3.0, 1.0)
        assert kelly <= 0.25

    def test_kelly_fraction_negative_win_rate(self):
        sizer = PositionSizer()
        assert sizer.calculate_kelly_fraction(0.0, 2.0, 1.0) == 0.0

    def test_kelly_fraction_win_rate_one(self):
        sizer = PositionSizer()
        assert sizer.calculate_kelly_fraction(1.0, 2.0, 1.0) == 0.0

    def test_kelly_fraction_avg_loss_zero(self):
        sizer = PositionSizer()
        assert sizer.calculate_kelly_fraction(0.6, 2.0, 0.0) == 0.0

    def test_kelly_fraction_avg_loss_negative(self):
        sizer = PositionSizer()
        assert sizer.calculate_kelly_fraction(0.6, 2.0, -1.0) == 0.0

    def test_kelly_fraction_fifty_fifty(self):
        sizer = PositionSizer()
        kelly = sizer.calculate_kelly_fraction(0.5, 1.0, 1.0)
        assert kelly == 0.0


class TestPositionSizerVar:
    def test_var_normal(self):
        sizer = PositionSizer()
        rets = [-0.05, -0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05]
        var = sizer.calculate_var(rets, 0.95)
        assert var > 0
        assert var == pytest.approx(0.045, abs=0.01)

    def test_var_empty_returns(self):
        sizer = PositionSizer()
        assert sizer.calculate_var([]) == 0.02

    def test_var_few_returns(self):
        sizer = PositionSizer()
        assert sizer.calculate_var([0.01, 0.02]) == 0.02

    def test_var_confidence_99(self):
        sizer = PositionSizer()
        rets = [-0.10, -0.08, -0.05, -0.03, -0.02, 0.0, 0.01, 0.02, 0.03, 0.04]
        var = sizer.calculate_var(rets, 0.99)
        assert var > 0.05

    def test_var_all_positive(self):
        sizer = PositionSizer()
        rets = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]
        var = sizer.calculate_var(rets, 0.95)
        assert var == 0.01


class TestPositionSizerVolatility:
    def test_volatility_position_normal(self):
        sizer = PositionSizer()
        prices = [
            {"high": 110, "low": 90, "close": 100},
            {"high": 115, "low": 95, "close": 105},
            {"high": 120, "low": 100, "close": 110},
        ]
        qty = sizer.calculate_volatility_position(prices)
        assert isinstance(qty, int)
        assert qty >= 1

    def test_volatility_position_empty(self):
        sizer = PositionSizer()
        assert sizer.calculate_volatility_position([]) == sizer.default_size

    def test_volatility_position_high_vol(self):
        sizer = PositionSizer()
        prices = [
            {"high": 101, "low": 99, "close": 100},
            {"high": 102, "low": 98, "close": 100},
        ]
        qty = sizer.calculate_volatility_position(prices)
        assert qty >= 1
        assert isinstance(qty, int)

    def test_volatility_position_tuple_input(self):
        sizer = PositionSizer()
        prices = [
            (100, 110, 90, 100),
            (105, 115, 95, 105),
        ]
        qty = sizer.calculate_volatility_position(prices)
        assert isinstance(qty, int)
        assert qty >= 1

    def test_volatility_position_zero_vol(self):
        sizer = PositionSizer()
        prices = [
            {"high": 100, "low": 100, "close": 100},
        ]
        qty = sizer.calculate_volatility_position(prices)
        assert qty == sizer.default_size
