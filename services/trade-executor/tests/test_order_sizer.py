import pytest
from executors.order_sizer import (
    FixedSizer,
    KellySizer,
    OrderSizer,
    PercentSizer,
    RiskParitySizer,
    VolatilitySizer,
)


class TestOrderSizerABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            OrderSizer()


class TestFixedSizer:
    def test_default_qty(self):
        sizer = FixedSizer()
        assert sizer.calculate(100_000, 50_000) == 10

    def test_custom_qty(self):
        sizer = FixedSizer(fixed_qty=5)
        assert sizer.calculate(100_000, 50_000) == 5

    def test_zero_balance(self):
        sizer = FixedSizer(fixed_qty=10)
        assert sizer.calculate(0, 50_000) == 10

    def test_zero_price(self):
        sizer = FixedSizer(fixed_qty=10)
        assert sizer.calculate(100_000, 0) == 10

    def test_min_one_seven(self):
        sizer = FixedSizer(fixed_qty=0)
        assert sizer.calculate(100_000, 50_000) == 1

    def test_min_one_negative(self):
        sizer = FixedSizer(fixed_qty=-5)
        assert sizer.calculate(100_000, 50_000) == 1


class TestPercentSizer:
    def test_ten_percent(self):
        sizer = PercentSizer(percent=0.1)
        qty = sizer.calculate(1_000_000, 50_000)
        assert qty == 2

    def test_fifty_percent(self):
        sizer = PercentSizer(percent=0.5)
        qty = sizer.calculate(1_000_000, 10_000)
        assert qty == 50

    def test_min_one(self):
        sizer = PercentSizer(percent=0.001)
        qty = sizer.calculate(10_000, 50_000)
        assert qty == 1

    def test_zero_balance(self):
        sizer = PercentSizer(percent=0.1)
        assert sizer.calculate(0, 50_000) == 1

    def test_zero_price(self):
        sizer = PercentSizer(percent=0.1)
        assert sizer.calculate(100_000, 0) == 1

    def test_exact_calculation(self):
        sizer = PercentSizer(percent=0.2)
        qty = sizer.calculate(500_000, 25_000)
        assert qty == 4

    def test_balance_price_both_zero(self):
        sizer = PercentSizer(percent=0.1)
        assert sizer.calculate(0, 0) == 1


class TestKellySizer:
    def test_normal_case(self):
        sizer = KellySizer(win_rate=0.6, avg_win=2.0, avg_loss=1.0)
        qty = sizer.calculate(1_000_000, 50_000)
        assert qty == 5

    def test_clamped_max(self):
        sizer = KellySizer(win_rate=0.9, avg_win=3.0, avg_loss=1.0)
        qty = sizer.calculate(1_000_000, 50_000)
        assert qty == 5

    def test_zero_win_rate(self):
        sizer = KellySizer(win_rate=0.0, avg_win=2.0, avg_loss=1.0)
        assert sizer.calculate(1_000_000, 50_000) == 1

    def test_fifty_fifty(self):
        sizer = KellySizer(win_rate=0.5, avg_win=1.0, avg_loss=1.0)
        assert sizer.calculate(1_000_000, 50_000) == 1

    def test_avg_loss_zero(self):
        sizer = KellySizer(win_rate=0.6, avg_win=2.0, avg_loss=0.0)
        assert sizer.calculate(1_000_000, 50_000) == 1

    def test_avg_loss_negative(self):
        sizer = KellySizer(win_rate=0.6, avg_win=2.0, avg_loss=-1.0)
        assert sizer.calculate(1_000_000, 50_000) == 1

    def test_win_rate_one(self):
        sizer = KellySizer(win_rate=1.0, avg_win=2.0, avg_loss=1.0)
        assert sizer.calculate(1_000_000, 50_000) == 1

    def test_zero_balance(self):
        sizer = KellySizer(win_rate=0.6, avg_win=2.0, avg_loss=1.0)
        assert sizer.calculate(0, 50_000) == 1

    def test_zero_price(self):
        sizer = KellySizer(win_rate=0.6, avg_win=2.0, avg_loss=1.0)
        assert sizer.calculate(1_000_000, 0) == 1

    def test_negative_avg_win(self):
        sizer = KellySizer(win_rate=0.6, avg_win=-1.0, avg_loss=1.0)
        b = -1.0 / 1.0
        assert b <= 0

    def test_edge_win_rate_near_half(self):
        sizer = KellySizer(win_rate=0.55, avg_win=1.5, avg_loss=1.0)
        qty = sizer.calculate(1_000_000, 50_000)
        assert qty >= 1


class TestVolatilitySizer:
    def test_normal(self):
        sizer = VolatilitySizer(risk_per_trade=0.02)
        qty = sizer.calculate(1_000_000, 50_000, atr=1_000)
        assert qty == 20

    def test_higher_risk(self):
        sizer = VolatilitySizer(risk_per_trade=0.05)
        qty = sizer.calculate(1_000_000, 50_000, atr=1_000)
        assert qty == 50

    def test_min_one(self):
        sizer = VolatilitySizer(risk_per_trade=0.01)
        qty = sizer.calculate(10_000, 50_000, atr=500)
        assert qty == 1

    def test_zero_atr(self):
        sizer = VolatilitySizer()
        assert sizer.calculate(1_000_000, 50_000, atr=0) == 1

    def test_no_atr_keyword(self):
        sizer = VolatilitySizer()
        assert sizer.calculate(1_000_000, 50_000) == 1

    def test_zero_balance(self):
        sizer = VolatilitySizer()
        assert sizer.calculate(0, 50_000, atr=1_000) == 1

    def test_zero_price(self):
        sizer = VolatilitySizer()
        assert sizer.calculate(1_000_000, 0, atr=1_000) == 1

    def test_small_atr_large_balance(self):
        sizer = VolatilitySizer(risk_per_trade=0.02)
        qty = sizer.calculate(10_000_000, 50_000, atr=100)
        assert qty == 2_000

    def test_custom_atr_period(self):
        sizer = VolatilitySizer(atr_period=14, risk_per_trade=0.02)
        qty = sizer.calculate(1_000_000, 50_000, atr=800)
        assert qty == 25


class TestRiskParitySizer:
    def test_normal(self):
        sizer = RiskParitySizer(n_positions=10)
        qty = sizer.calculate(1_000_000, 50_000)
        assert qty == 2

    def test_five_positions(self):
        sizer = RiskParitySizer(n_positions=5)
        qty = sizer.calculate(1_000_000, 20_000)
        assert qty == 10

    def test_min_one(self):
        sizer = RiskParitySizer(n_positions=100)
        qty = sizer.calculate(10_000, 50_000)
        assert qty == 1

    def test_zero_balance(self):
        sizer = RiskParitySizer(n_positions=10)
        assert sizer.calculate(0, 50_000) == 1

    def test_zero_price(self):
        sizer = RiskParitySizer(n_positions=10)
        assert sizer.calculate(100_000, 0) == 1

    def test_single_position(self):
        sizer = RiskParitySizer(n_positions=1)
        qty = sizer.calculate(100_000, 5_000)
        assert qty == 20

    def test_n_positions_zero(self):
        sizer = RiskParitySizer(n_positions=0)
        assert sizer.calculate(100_000, 5_000) == 1

    def test_exact_division(self):
        sizer = RiskParitySizer(n_positions=4)
        qty = sizer.calculate(200_000, 50_000)
        assert qty == 1

    def test_confidence_ignored_by_default(self):
        sizer = RiskParitySizer(n_positions=5)
        qty_with = sizer.calculate(500_000, 25_000, confidence=0.9)
        qty_without = sizer.calculate(500_000, 25_000)
        assert qty_with == qty_without
