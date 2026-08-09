"""LowVolatilityStrategy 테스트 — 252일 연표준편차·베타 계산 및 랭크 검증."""

import math

import pytest

from app.strategies.lowvol_strategy import LowVolatilityStrategy

REBALANCE_DATE = "2024-03-31"
INDEX_CODE = "000001"


def _prices_from_returns(returns, base=100.0):
    prices = [base]
    for r in returns:
        prices.append(prices[-1] * math.exp(r))
    return prices


def _alt(step, n=252, phase=1):
    return [phase * step if i % 2 == 0 else -phase * step for i in range(n)]


class MockStorage:
    def __init__(self, stocks, series_map, positions_all=False):
        self.stocks = stocks
        self.series_map = series_map
        self.positions_all = positions_all

    def get_strategy_config(self, name):
        return {}

    def get_all_stocks(self, limit=None):
        return [{"stock_code": c} for c in self.stocks]

    def get_market_caps(self):
        return {c: 1e13 for c in self.stocks}

    def get_avg_trading_value(self, stock_code, days=30):
        return 1e10

    def get_first_trade_date(self, stock_code):
        return "2020-01-02"

    def get_positions(self):
        if not self.positions_all:
            return []
        return [{"stock_code": c} for c in self.stocks]

    def get_financial_statements(self, stock_code, asof_date=None):
        # 유니버스 필터가 요구하는 최소 1개 분기 재무 데이터
        return [{
            "stock_code": stock_code,
            "report_date": "2024-03-31",
            "net_income": 1e12,
            "total_equity": 2e12,
        }]

    def get_price_series_asof(self, stock_code, days=252, asof_date=None):
        return self.series_map.get(stock_code, [])[-days:]


def _make_strategy(stocks, series_map, positions_all=False, market_index_code=INDEX_CODE):
    storage = MockStorage(stocks, series_map, positions_all=positions_all)
    config = {"rebalance_anchor": REBALANCE_DATE, "market_index_code": market_index_code}
    return LowVolatilityStrategy(storage, config=config)


def _default_fixture():
    """0000: 저변동(beta 0.5), 0001: 고변동(beta 2.0), 0002: 음수 베타(-1), 0003: 이력 부족."""
    index_series = _prices_from_returns(_alt(0.02))
    return {
        "stocks": ["STK0000", "STK0001", "STK0002", "STK0003"],
        "series_map": {
            INDEX_CODE: index_series,
            "STK0000": _prices_from_returns(_alt(0.01)),
            "STK0001": _prices_from_returns(_alt(0.04)),
            "STK0002": _prices_from_returns(_alt(0.02, phase=-1)),
            "STK0003": [100.0, 105.0],  # 수익률 1개 → 팩터 산출 불가
        },
    }


def test_annualized_vol_exact():
    strategy = _make_strategy([], {})
    returns = _alt(0.01)
    vol = strategy._annualized_vol(returns)
    assert vol == pytest.approx(0.159062, abs=1e-4)  # std(±0.01)×√252


def test_beta_exact():
    strategy = _make_strategy([], {})
    m = _alt(0.02)
    assert strategy._beta(_alt(0.01), m) == pytest.approx(0.5)
    assert strategy._beta(_alt(0.04), m) == pytest.approx(2.0)
    assert strategy._beta(_alt(0.02, phase=-1), m) == pytest.approx(-1.0)
    assert strategy._beta([0.01], [0.02]) is None  # 수익률 1개 → None


def test_happy_path_two_lowvol_buys_top_ranked():
    f = _default_fixture()
    strategy = _make_strategy(f["stocks"], f["series_map"])
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    buys = [s for s in signals if s["action"] == "buy"]
    assert len(buys) == 2
    assert {s["stock_code"] for s in buys} == {"STK0000", "STK0001"}
    conf = {s["stock_code"]: s["confidence"] for s in buys}
    assert conf["STK0000"] > conf["STK0001"]
    for s in buys:
        assert s["strategy_name"] == "lowvol_factor"
        assert s["price"] == 0
        assert 0.5 <= s["confidence"] <= 0.95


def test_negative_beta_stock_excluded():
    f = _default_fixture()
    strategy = _make_strategy(f["stocks"], f["series_map"])
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    codes = {s["stock_code"] for s in signals}
    assert "STK0002" not in codes


def test_short_history_stock_excluded():
    f = _default_fixture()
    strategy = _make_strategy(f["stocks"], f["series_map"])
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    codes = {s["stock_code"] for s in signals}
    assert "STK0003" not in codes


def test_market_fallback_universe_average():
    f = _default_fixture()
    strategy = _make_strategy(f["stocks"], f["series_map"], market_index_code="999999")
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    buys = {s["stock_code"] for s in signals if s["action"] == "buy"}
    assert buys == {"STK0000", "STK0001"}  # 0002는 음수 베타로 제외, 0003 이력 부족
    avg = strategy._universe_avg_returns({"A": [0.01, -0.01], "B": [0.02]})
    assert avg == pytest.approx([0.015, -0.01])


def test_no_market_returns_returns_empty():
    strategy = _make_strategy(["STK0000"], {})  # 지수·가격 모두 없음
    assert strategy.analyze(asof_date=REBALANCE_DATE) == []


def test_non_rebalance_day_returns_empty():
    f = _default_fixture()
    strategy = _make_strategy(f["stocks"], f["series_map"])
    assert strategy.analyze(asof_date="2024-04-01") == []


def test_no_positions_no_sell():
    f = _default_fixture()
    strategy = _make_strategy(f["stocks"], f["series_map"])
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    assert all(s["action"] == "buy" for s in signals)


def test_empty_universe_returns_empty():
    strategy = _make_strategy([], {})
    assert strategy.analyze(asof_date=REBALANCE_DATE) == []
