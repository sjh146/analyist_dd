"""MomentumStrategy 테스트 — 12-1/3-6/52주 팩터 계산 및 상위 30 랭크 검증."""

import pytest

from app.strategies.momentum_strategy import MomentumStrategy

REBALANCE_DATE = "2024-03-31"


def _pattern_series(p_t=500.0, p_252=100.0, p_126=200.0, p_63=300.0, p_21=400.0, spike_at=None):
    """253일 시리즈: 지정 지점 가격을 고정, 나머지는 100+i (정확한 값 assert용)."""
    vals = [100.0 + i for i in range(253)]
    vals[0] = p_252
    vals[126] = p_126
    vals[189] = p_63
    vals[231] = p_21
    vals[252] = p_t
    if spike_at is not None:
        vals[spike_at] = 600.0
    return vals


class MockStorage:
    def __init__(self, series_map, positions_all=False):
        self.series_map = series_map
        self.positions_all = positions_all

    def get_strategy_config(self, name):
        return {}

    def get_all_stocks(self, limit=None):
        return [{"stock_code": c} for c in self.series_map]

    def get_market_caps(self):
        return {c: 1e13 for c in self.series_map}

    def get_avg_trading_value(self, stock_code, days=30):
        return 1e10

    def get_first_trade_date(self, stock_code):
        return "2020-01-02"

    def get_positions(self):
        if not self.positions_all:
            return []
        return [{"stock_code": c} for c in self.series_map]

    def get_price_series_asof(self, stock_code, days=252, asof_date=None):
        return self.series_map.get(stock_code, [])[-days:]

    def get_financial_statements(self, stock_code, asof_date=None):
        # 유니버스 필터가 요구하는 최소 1개 분기 재무 데이터
        return [{
            "stock_code": stock_code,
            "report_date": "2024-03-31",
            "net_income": 1e12,
            "total_equity": 2e12,
        }]


def _make_strategy(series_map, positions_all=False):
    storage = MockStorage(series_map, positions_all=positions_all)
    return MomentumStrategy(storage, config={"rebalance_anchor": REBALANCE_DATE})


def test_momentum_factor_values_exact():
    strategy = _make_strategy({})
    m12_1, m3_6, m52w = strategy._momentum_factors(_pattern_series())
    assert m12_1 == pytest.approx(3.75)      # (500/100) - (500/400)
    assert m3_6 == pytest.approx(-0.8333333)  # (500/300) - (500/200)
    assert m52w == pytest.approx(1.0)        # 500 / max(500)


def test_short_history_excluded():
    strategy = _make_strategy({})
    assert strategy._momentum_factors([100.0] * 252) == (None, None, None)
    assert strategy._momentum_factors([100.0] * 100) == (None, None, None)
    assert strategy._momentum_factors([]) == (None, None, None)


def test_zero_or_negative_base_price_guard():
    strategy = _make_strategy({})
    # P_{t-252} = 0 → 12-1만 None, 나머지 팩터는 계산 유지
    m12_1, m3_6, m52w = strategy._momentum_factors(_pattern_series(p_252=0.0))
    assert m12_1 is None
    assert m3_6 == pytest.approx(-0.8333333)
    assert m52w == pytest.approx(1.0)


def test_happy_path_two_buys_top_ranked():
    series_map = {
        "STK0000": _pattern_series(),                      # 강한 모멘텀
        "STK0001": _pattern_series(p_252=200.0, p_63=250.0, p_21=300.0, spike_at=100),  # 약한 모멘텀
        "STK0002": [100.0 + i for i in range(100)],        # 이력 부족 → 제외
        "STK0003": [100.0 + i for i in range(100)],        # 이력 부족 → 제외
    }
    strategy = _make_strategy(series_map)
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    buys = [s for s in signals if s["action"] == "buy"]
    assert len(buys) == 2
    assert {s["stock_code"] for s in buys} == {"STK0000", "STK0001"}
    conf = {s["stock_code"]: s["confidence"] for s in buys}
    assert conf["STK0000"] > conf["STK0001"]
    for s in buys:
        assert s["strategy_name"] == "momentum_factor"
        assert s["price"] == 0
        assert 0.5 <= s["confidence"] <= 0.95


def test_short_history_stocks_not_in_signals():
    series_map = {
        "STK0000": _pattern_series(),
        "STK0001": [100.0 + i for i in range(100)],
    }
    strategy = _make_strategy(series_map)
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    codes = {s["stock_code"] for s in signals}
    assert "STK0001" not in codes
    assert "STK0000" in codes


def test_non_rebalance_day_returns_empty():
    strategy = _make_strategy({"STK0000": _pattern_series()})
    assert strategy.analyze(asof_date="2024-04-01") == []


def test_no_positions_no_sell():
    strategy = _make_strategy({"STK0000": _pattern_series()})
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    assert all(s["action"] == "buy" for s in signals)


def test_empty_universe_returns_empty():
    strategy = _make_strategy({})
    assert strategy.analyze(asof_date=REBALANCE_DATE) == []


def test_sell_signal_when_held_but_dropped():
    series_map = {
        "STK0000": _pattern_series(),
        "STK0001": _pattern_series(p_252=200.0, p_63=250.0, p_21=300.0, spike_at=100),
        "STK0002": [100.0 + i for i in range(100)],
        "STK0003": [100.0 + i for i in range(100)],
    }
    strategy = _make_strategy(series_map, positions_all=True)
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    sells = [s for s in signals if s["action"] == "sell"]
    assert len(sells) == 2
    assert {s["stock_code"] for s in sells} == {"STK0002", "STK0003"}
    for s in sells:
        assert s["confidence"] == 0.6
        assert s["strategy_name"] == "momentum_factor"
