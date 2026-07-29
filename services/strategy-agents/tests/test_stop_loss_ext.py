"""
Tests for StopLoss extension methods (trailing, volatility, time stop).
"""

import pytest
from datetime import datetime, timedelta
from app.risk_management.stop_loss import StopLoss, SELL_SIGNAL, HOLD_SIGNAL


class TestStopLossTrailing:
    def test_trailing_stop_hold(self):
        sl = StopLoss()
        pos = {"stock_code": "005930", "avg_price": 10000}
        result = sl.trailing_stop(pos, 10500, highest_price=11000)
        assert result["action"] == HOLD_SIGNAL

    def test_trailing_stop_trigger(self):
        sl = StopLoss()
        pos = {"stock_code": "005930", "avg_price": 10000}
        result = sl.trailing_stop(pos, 9000, highest_price=11000, trail_pct=0.07)
        assert result["action"] == SELL_SIGNAL

    def test_trailing_stop_exact_boundary(self):
        sl = StopLoss()
        pos = {"stock_code": "005930", "avg_price": 10000}
        highest = 10000
        stop_price = highest * (1.0 - 0.07)
        result = sl.trailing_stop(pos, stop_price, highest_price=highest, trail_pct=0.07)
        assert result["action"] == SELL_SIGNAL

    def test_trailing_stop_no_highest(self):
        sl = StopLoss()
        pos = {"stock_code": "005930", "avg_price": 10000}
        result = sl.trailing_stop(pos, 10500)
        assert result["action"] == HOLD_SIGNAL


class TestStopLossVolatility:
    def test_volatility_stop_hold(self):
        sl = StopLoss()
        pos = {"stock_code": "005930", "avg_buy_price": 10000}
        result = sl.volatility_stop(pos, 9700, atr=200, multiplier=2.0)
        assert result["action"] == HOLD_SIGNAL

    def test_volatility_stop_trigger(self):
        sl = StopLoss()
        pos = {"stock_code": "005930", "avg_buy_price": 10000}
        result = sl.volatility_stop(pos, 8999, atr=500, multiplier=2.0)
        assert result["action"] == SELL_SIGNAL

    def test_volatility_stop_no_entry(self):
        sl = StopLoss()
        pos = {"stock_code": "005930", "avg_buy_price": 0}
        result = sl.volatility_stop(pos, 9500, atr=200)
        assert result["action"] == HOLD_SIGNAL


class TestStopLossTime:
    def test_time_stop_hold(self):
        sl = StopLoss()
        pos = {"stock_code": "005930", "entry_date": datetime.now().strftime("%Y-%m-%d")}
        result = sl.time_stop(pos, max_hold_days=20)
        assert result["action"] == HOLD_SIGNAL

    def test_time_stop_no_entry_date(self):
        sl = StopLoss()
        pos = {"stock_code": "005930"}
        result = sl.time_stop(pos)
        assert result["action"] == HOLD_SIGNAL

    def test_time_stop_trigger(self):
        sl = StopLoss()
        past_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        pos = {"stock_code": "005930", "entry_date": past_date}
        result = sl.time_stop(pos, max_hold_days=20)
        assert result["action"] == SELL_SIGNAL

    def test_time_stop_datetime_object(self):
        sl = StopLoss()
        past_date = datetime.now() - timedelta(days=25)
        pos = {"stock_code": "005930", "entry_date": past_date}
        result = sl.time_stop(pos, max_hold_days=20)
        assert result["action"] == SELL_SIGNAL
