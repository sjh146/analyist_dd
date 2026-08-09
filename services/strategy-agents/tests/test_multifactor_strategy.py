"""MultiFactorStrategy 테스트 — 팩터 Z-score 동일가중 합산·상위 20 랭크 검증."""

import pytest

from app.strategies.multifactor_strategy import MultiFactorStrategy

REBALANCE_DATE = "2024-03-31"
QUARTERS = [
    "2022-06-30", "2022-09-30", "2022-12-31", "2023-03-31",
    "2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31",
]


class MockStorage:
    def __init__(self, n_stocks=30, positions_all=False, financial_missing=None):
        self.n = n_stocks
        self.positions_all = positions_all
        self.financial_missing = set(financial_missing or [])

    def get_strategy_config(self, name):
        return {}

    def get_all_stocks(self, limit=None):
        return [{"stock_code": f"STK{i:04d}"} for i in range(self.n)]

    def get_market_caps(self):
        return {f"STK{i:04d}": 1e13 for i in range(self.n)}

    def get_avg_trading_value(self, stock_code, days=30):
        return 1e10

    def get_first_trade_date(self, stock_code):
        return "2020-01-02"

    def get_positions(self):
        if not self.positions_all:
            return []
        return [{"stock_code": f"STK{i:04d}"} for i in range(self.n)]

    def get_financial_statements(self, stock_code, asof_date=None):
        i = int(stock_code[3:])
        if i >= self.n or i in self.financial_missing:
            return []
        rows = []
        for qi, q in enumerate(QUARTERS):
            ni = 100 + i * 10 if qi < 4 else 105 + i * 15
            rows.append({
                "stock_code": stock_code,
                "report_date": q,
                "net_income": ni,
                "total_equity": 1000 + i * 50,
                "revenue": 1000 + i * 100,
                "gross_profit": 500 + i * 10,
                "total_assets": 2000,
            })
        if asof_date:
            asof = str(asof_date) if not hasattr(asof_date, "isoformat") else asof_date.isoformat()
            rows = [r for r in rows if r["report_date"] <= asof]
        return sorted(rows, key=lambda r: r["report_date"])

    def get_price_series_asof(self, stock_code, days=252, asof_date=None):
        if stock_code == "000001":
            return []  # 지수 종목 없음 → 유니버스 평균 fallback
        i = int(stock_code[3:])
        if i >= self.n:
            return []
        return self._price_series(i)[-days:]

    @staticmethod
    def _price_series(i):
        vals = [100.0 + j for j in range(253)]
        vals[0] = 100.0
        vals[126] = 150.0 + i * 2
        vals[189] = 100.0
        vals[231] = 200.0 + i * 10
        vals[252] = 500.0 + i * 10
        vals[100] = 600.0 + i * 2
        return vals


def _make_strategy(storage):
    return MultiFactorStrategy(storage, config={"rebalance_anchor": REBALANCE_DATE})


def test_combine_z_exact():
    strategy = _make_strategy(MockStorage(n_stocks=0))
    factors = {
        "A": {"per": 10, "roe": 0.15},
        "B": {"per": 20, "roe": 0.10},
        "C": {"per": 30, "roe": 0.05},
    }
    combined = strategy._combine_z(factors)
    assert combined["A"] == pytest.approx(2.44949, abs=1e-4)
    assert combined["B"] == pytest.approx(0.0, abs=1e-4)
    assert combined["C"] == pytest.approx(-2.44949, abs=1e-4)


def test_combine_z_degenerate_factor_skipped():
    strategy = _make_strategy(MockStorage(n_stocks=0))
    base = {
        "A": {"per": 10, "roe": 0.15},
        "B": {"per": 20, "roe": 0.10},
        "C": {"per": 30, "roe": 0.05},
    }
    with_const = {c: dict(f, const=1.0) for c, f in base.items()}
    assert strategy._combine_z(with_const) == pytest.approx(strategy._combine_z(base))


def test_combine_z_missing_factor_ok():
    strategy = _make_strategy(MockStorage(n_stocks=0))
    factors = {
        "A": {"per": 10, "roe": 0.15},
        "B": {"per": 20},
        "C": {"roe": 0.05},
    }
    combined = strategy._combine_z(factors)
    assert combined["A"] == pytest.approx(2.0)
    assert combined["B"] == pytest.approx(-1.0)
    assert combined["C"] == pytest.approx(-1.0)


def test_happy_path_top20_buy_and_10_sell():
    storage = MockStorage(n_stocks=30, positions_all=True)
    strategy = _make_strategy(storage)
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    buys = [s for s in signals if s["action"] == "buy"]
    sells = [s for s in signals if s["action"] == "sell"]
    assert len(buys) == 20
    assert len(sells) == 10
    for s in buys:
        assert s["strategy_name"] == "multifactor"
        assert s["price"] == 0
        assert 0.5 <= s["confidence"] <= 0.95


def test_high_momentum_stocks_top_ranked():
    storage = MockStorage(n_stocks=30)
    strategy = _make_strategy(storage)
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    buys = {s["stock_code"] for s in signals if s["action"] == "buy"}
    assert "STK0028" in buys and "STK0029" in buys
    assert "STK0000" not in buys and "STK0001" not in buys


def test_non_rebalance_day_returns_empty():
    storage = MockStorage(n_stocks=30)
    strategy = _make_strategy(storage)
    assert strategy.analyze(asof_date="2024-04-01") == []


def test_no_positions_no_sell():
    storage = MockStorage(n_stocks=30)
    strategy = _make_strategy(storage)
    signals = strategy.analyze(asof_date=REBALANCE_DATE)
    assert all(s["action"] == "buy" for s in signals)
    assert len(signals) == 20


def test_all_missing_financials_returns_empty():
    storage = MockStorage(n_stocks=30, financial_missing=set(range(30)))
    strategy = _make_strategy(storage)
    assert strategy.analyze(asof_date=REBALANCE_DATE) == []


def test_empty_universe_returns_empty():
    strategy = _make_strategy(MockStorage(n_stocks=0))
    assert strategy.analyze(asof_date=REBALANCE_DATE) == []


def test_sell_signal_shape_when_held():
    storage = MockStorage(n_stocks=30, positions_all=True)
    strategy = _make_strategy(storage)
    sells = [s for s in strategy.analyze(asof_date=REBALANCE_DATE) if s["action"] == "sell"]
    assert len(sells) == 10
    for s in sells:
        assert s["confidence"] == 0.6
        assert s["strategy_name"] == "multifactor"
