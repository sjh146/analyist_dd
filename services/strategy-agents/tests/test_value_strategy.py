"""Unit tests for ValueStrategy (low PER/PBR/PSR, top-30 rebalance)."""

import pytest

from app.strategies.value_strategy import ValueStrategy

REBALANCE_DATE = "2024-03-31"  # anchor day -> days % 63 == 0
NON_REBALANCE_DATE = "2024-04-01"  # days=1 -> not on cycle


class MockStorage:
    """Storage stub for universe filter + value_ratios + positions."""

    def __init__(self, n_stocks=40, financial_missing=None, positions_all=True):
        self.n_stocks = n_stocks
        self.financial_missing = set(financial_missing or [])
        self.positions_all = positions_all

    def _code(self, i):
        return f"{i:06d}"

    def get_all_stocks(self, limit=None):
        return [{"stock_code": self._code(i)} for i in range(self.n_stocks)]

    def get_market_caps(self):
        return {self._code(i): 1e13 for i in range(self.n_stocks)}

    def get_avg_trading_value(self, stock_code, days=30):
        return 5e9

    def get_first_trade_date(self, stock_code):
        return "2020-01-02"

    def get_financial_statements(self, stock_code, asof_date=None):
        i = int(stock_code)
        if i >= self.n_stocks or i in self.financial_missing:
            return []
        # Higher i -> higher income/equity/revenue -> lower PER/PBR/PSR -> better rank
        return [{
            "stock_code": stock_code,
            "report_date": "2024-03-31",
            "net_income": 1e12 + i * 1e10,
            "total_equity": 2e12 + i * 1e10,
            "revenue": 5e12 + i * 1e10,
            "total_assets": 1e13,
            "gross_profit": 3e12 + i * 1e10,
        }]

    def get_positions(self):
        if not self.positions_all:
            return []
        return [{"stock_code": self._code(i), "quantity": 1} for i in range(self.n_stocks)]

    def get_strategy_config(self, strategy_name):
        return None


def make_strategy(storage, mode="per_pbr", config=None):
    base = {"mode": mode, "rebalance_interval_days": 63, "rebalance_anchor": "2024-03-31"}
    if config:
        base.update(config)
    return ValueStrategy(storage, mode=mode, config=base)


class TestValueStrategyHappy:
    def test_per_pbr_top30_buy_rest_sell(self):
        strategy = make_strategy(MockStorage(40))
        signals = strategy.analyze(asof_date=REBALANCE_DATE)
        buys = [s for s in signals if s["action"] == "buy"]
        sells = [s for s in signals if s["action"] == "sell"]
        assert len(buys) == 30
        assert len(sells) == 10
        # Best 30 (highest income/equity -> indices 10..39) bought
        assert {s["stock_code"] for s in buys} == {f"{i:06d}" for i in range(10, 40)}
        # Worst 10 (indices 0..9) dropped -> sell
        assert {s["stock_code"] for s in sells} == {f"{i:06d}" for i in range(10)}

    def test_psr_mode_top30(self):
        strategy = make_strategy(MockStorage(40), mode="psr")
        signals = strategy.analyze(asof_date=REBALANCE_DATE)
        buys = [s for s in signals if s["action"] == "buy"]
        assert len(buys) == 30
        assert {s["stock_code"] for s in buys} == {f"{i:06d}" for i in range(10, 40)}

    def test_signal_shape(self):
        strategy = make_strategy(MockStorage(40))
        signals = strategy.analyze(asof_date=REBALANCE_DATE)
        buy = signals[0]
        assert buy["price"] == 0
        assert buy["strategy_name"] == "value_factor"
        assert buy["action"] in ("buy", "sell")
        assert 0.5 <= buy["confidence"] <= 0.95

    def test_sell_only_for_held_stocks(self):
        storage = MockStorage(40, positions_all=False)
        strategy = make_strategy(storage)
        signals = strategy.analyze(asof_date=REBALANCE_DATE)
        assert all(s["action"] == "buy" for s in signals)
        assert len(signals) == 30

    def test_rebalance_gate_disabled(self):
        strategy = make_strategy(MockStorage(40), config={"rebalance_interval_days": 0})
        signals = strategy.analyze(asof_date="2024-05-15")
        assert len(signals) == 40


class TestValueStrategyFailure:
    def test_non_rebalance_day_no_signals(self):
        strategy = make_strategy(MockStorage(40))
        assert strategy.analyze(asof_date=NON_REBALANCE_DATE) == []

    def test_no_financial_data_stock_excluded_no_error(self):
        storage = MockStorage(40, financial_missing={0, 39}, positions_all=False)
        strategy = make_strategy(storage)
        signals = strategy.analyze(asof_date=REBALANCE_DATE)
        buys = [s for s in signals if s["action"] == "buy"]
        # 38 ranked -> top 30 bought; missing-financial stocks never bought
        assert "000000" not in {s["stock_code"] for s in buys}
        assert "000039" not in {s["stock_code"] for s in buys}
        assert len(buys) == 30

    def test_all_stocks_lack_financials_no_signals(self):
        strategy = make_strategy(MockStorage(40, financial_missing=set(range(40))))
        assert strategy.analyze(asof_date=REBALANCE_DATE) == []

    def test_empty_universe_no_signals(self):
        strategy = make_strategy(MockStorage(0))
        assert strategy.analyze(asof_date=REBALANCE_DATE) == []
