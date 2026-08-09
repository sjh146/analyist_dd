"""Unit tests for the universe filter (T2)."""

import pytest

from app.factors.universe import (
    MIN_AVG_TRADING_VALUE,
    MIN_MARKET_CAP,
    filter_universe,
)


class MockStorage:
    def __init__(self, market_caps=None, trading_values=None, financials=None, first_dates=None):
        self._market_caps = market_caps or {}
        self._trading_values = trading_values or {}
        self._financials = financials or {}
        self._first_dates = first_dates or {}

    def get_market_caps(self):
        return dict(self._market_caps)

    def get_avg_trading_value(self, stock_code, days=30):
        return self._trading_values.get(stock_code)

    def get_financial_statements(self, stock_code, asof_date=None):
        return [dict(r) for r in self._financials.get(stock_code, [])]

    def get_first_trade_date(self, stock_code):
        return self._first_dates.get(stock_code)


def make_stock(code):
    return {"stock_code": code, "stock_name": f"stock_{code}", "market": "KOSPI"}


def make_financials(n_quarters):
    return [
        {"stock_code": "X", "report_date": f"2024-0{q}-15", "revenue": 100}
        for q in range(1, n_quarters + 1)
    ]


BASE_STOCKS = [make_stock("A"), make_stock("B"), make_stock("C"), make_stock("D")]


def base_storage():
    """All four stocks pass every criterion; tests mutate one at a time."""
    return MockStorage(
        market_caps={"A": 1e12, "B": 1e12, "C": 1e12, "D": 1e12},
        trading_values={"A": 1e10, "B": 1e10, "C": 1e10, "D": 1e10},
        financials={c: make_financials(3) for c in ["A", "B", "C", "D"]},
        first_dates={c: "2020-01-02" for c in ["A", "B", "C", "D"]},
    )


class TestUniverseHappy:
    def test_all_passing_stocks_kept(self):
        codes = filter_universe(base_storage(), BASE_STOCKS, asof_date="2024-06-30")
        assert set(codes) == {"A", "B", "C", "D"}

    def test_low_market_cap_excluded(self):
        storage = base_storage()
        storage._market_caps["A"] = 1e9
        codes = filter_universe(storage, BASE_STOCKS, asof_date="2024-06-30")
        assert "A" not in codes
        assert set(codes) == {"B", "C", "D"}

    def test_market_cap_at_boundary_kept(self):
        storage = base_storage()
        storage._market_caps["A"] = MIN_MARKET_CAP
        codes = filter_universe(storage, BASE_STOCKS, asof_date="2024-06-30")
        assert "A" in codes

    def test_low_trading_value_excluded(self):
        storage = base_storage()
        storage._trading_values["B"] = 1e8
        codes = filter_universe(storage, BASE_STOCKS, asof_date="2024-06-30")
        assert "B" not in codes
        assert set(codes) == {"A", "C", "D"}

    def test_trading_value_at_boundary_kept(self):
        storage = base_storage()
        storage._trading_values["B"] = MIN_AVG_TRADING_VALUE
        codes = filter_universe(storage, BASE_STOCKS, asof_date="2024-06-30")
        assert "B" in codes

    def test_no_financials_excluded(self):
        storage = base_storage()
        storage._financials["C"] = []
        codes = filter_universe(storage, BASE_STOCKS, asof_date="2024-06-30")
        assert "C" not in codes

    def test_short_listing_history_excluded(self):
        storage = base_storage()
        storage._first_dates["D"] = "2024-06-01"
        codes = filter_universe(storage, BASE_STOCKS, asof_date="2024-06-30")
        assert "D" not in codes


class TestUniverseFailure:
    def test_null_market_cap_silently_excluded(self):
        storage = base_storage()
        storage._market_caps["A"] = None
        codes = filter_universe(storage, BASE_STOCKS, asof_date="2024-06-30")
        assert "A" not in codes
        assert set(codes) == {"B", "C", "D"}

    def test_null_trading_value_silently_excluded(self):
        storage = base_storage()
        storage._trading_values["B"] = None
        codes = filter_universe(storage, BASE_STOCKS, asof_date="2024-06-30")
        assert "B" not in codes

    def test_empty_stocks_no_error(self):
        assert filter_universe(base_storage(), [], asof_date="2024-06-30") == []

    def test_unknown_stock_in_list_no_error(self):
        storage = base_storage()
        stocks = BASE_STOCKS + [make_stock("ZZZ")]
        codes = filter_universe(storage, stocks, asof_date="2024-06-30")
        assert "ZZZ" not in codes
        assert set(codes) == {"A", "B", "C", "D"}

    def test_asof_none_uses_today_no_error(self):
        codes = filter_universe(base_storage(), BASE_STOCKS)
        assert set(codes) == {"A", "B", "C", "D"}
