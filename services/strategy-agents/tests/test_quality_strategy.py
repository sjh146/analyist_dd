"""QualityStrategy 테스트 — 2개 분기 ROE 평균·GP/A·이익 안정성 정렬 검증."""

import pytest

from app.strategies.quality_strategy import QualityStrategy

REBALANCE_DATE = "2024-03-31"
QUARTERS = [
    "2022-06-30", "2022-09-30", "2022-12-31", "2023-03-31",
    "2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31",
]


def _financial_rows(i, gross_profit_missing):
    """8개 분기 재무 데이터. 모든 팩터(ROE·GP/A·이익안정성)가 i에 단조 증가."""
    rows = []
    for qi, q in enumerate(QUARTERS):
        ni = 100 + i * 10 if qi < 4 else 105 + i * 15
        gp = None if i in gross_profit_missing else 500 + i * 10
        rows.append({
            "stock_code": f"STK{i:04d}",
            "report_date": q,
            "net_income": ni,
            "total_equity": 1000,
            "gross_profit": gp,
            "total_assets": 2000,
        })
    return rows


class MockStorage:
    def __init__(self, n_stocks=40, positions_all=False, financial_missing=None,
                 gross_profit_missing=None):
        self.n = n_stocks
        self.positions_all = positions_all
        self.financial_missing = set(financial_missing or [])
        self.gross_profit_missing = set(gross_profit_missing or [])

    def get_strategy_config(self, name):
        return {}

    def get_all_stocks(self, limit=None):
        return [{"stock_code": f"STK{i:04d}"} for i in range(self.n)]

    def get_market_caps(self):
        return {f"STK{i:04d}": 1e13 for i in range(self.n)}

    def get_avg_trading_value(self, code, days=30):
        return 1e10

    def get_first_trade_date(self, code):
        return "2020-01-02"

    def get_positions(self):
        if not self.positions_all:
            return []
        return [{"stock_code": f"STK{i:04d}"} for i in range(self.n)]

    def get_financial_statements(self, code, asof_date=None):
        i = int(code[3:])
        if i in self.financial_missing:
            return []
        rows = _financial_rows(i, self.gross_profit_missing)
        if asof_date:
            asof = str(asof_date) if not hasattr(asof_date, "isoformat") else asof_date.isoformat()
            rows = [r for r in rows if r["report_date"] <= asof]
        return sorted(rows, key=lambda r: r["report_date"])


@pytest.fixture
def storage():
    return MockStorage(n_stocks=40)


@pytest.fixture
def strategy():
    return QualityStrategy(MockStorage(n_stocks=40), config={"rebalance_anchor": REBALANCE_DATE})


def _signals(strategy, asof_date=REBALANCE_DATE):
    return strategy.analyze(asof_date=asof_date)


def test_happy_path_top30_buy_and_10_sell(storage):
    storage.positions_all = True
    strategy = QualityStrategy(storage, config={"rebalance_anchor": REBALANCE_DATE})
    signals = _signals(strategy)
    buys = [s for s in signals if s["action"] == "buy"]
    sells = [s for s in signals if s["action"] == "sell"]
    assert len(buys) == 30
    assert len(sells) == 10
    bought = {s["stock_code"] for s in buys}
    assert bought == {f"STK{i:04d}" for i in range(10, 40)}
    for s in buys:
        assert s["strategy_name"] == "quality_factor"
        assert s["price"] == 0
        assert 0.5 <= s["confidence"] <= 0.95


def test_highest_roe_two_stocks_top_ranked(strategy):
    signals = _signals(strategy)
    buys = {s["stock_code"]: s for s in signals if s["action"] == "buy"}
    assert "STK0038" in buys and "STK0039" in buys
    assert buys["STK0039"]["confidence"] == max(s["confidence"] for s in buys.values())


def test_gross_profit_missing_stock_still_ranked_no_error(strategy):
    strategy.storage.gross_profit_missing = {39}
    signals = _signals(strategy)
    buys = {s["stock_code"] for s in signals if s["action"] == "buy"}
    # ROE·이익안정성만으로 랭크되어 1등 유지 (GP/A 비중 0)
    assert "STK0039" in buys


def test_all_gross_profit_missing_factor_weight_zero(storage):
    storage.gross_profit_missing = set(range(40))
    strategy = QualityStrategy(storage, config={"rebalance_anchor": REBALANCE_DATE})
    signals = _signals(strategy)
    buys = [s for s in signals if s["action"] == "buy"]
    assert len(buys) == 30  # ROE·이익안정성만으로 결합, 에러 없음


def test_non_rebalance_day_returns_empty(strategy):
    assert _signals(strategy, asof_date="2024-04-01") == []


def test_empty_universe_returns_empty():
    storage = MockStorage(n_stocks=0)
    strategy = QualityStrategy(storage, config={"rebalance_anchor": REBALANCE_DATE})
    assert _signals(strategy) == []


def test_no_positions_no_sell(strategy):
    signals = _signals(strategy)
    assert all(s["action"] == "buy" for s in signals)
    assert len(signals) == 30


def test_all_missing_financials_returns_empty(storage):
    storage.financial_missing = set(range(40))
    strategy = QualityStrategy(storage, config={"rebalance_anchor": REBALANCE_DATE})
    assert _signals(strategy) == []


def test_sell_signal_shape_when_held():
    storage = MockStorage(n_stocks=40, positions_all=True)
    strategy = QualityStrategy(storage, config={"rebalance_anchor": REBALANCE_DATE})
    sells = [s for s in _signals(strategy) if s["action"] == "sell"]
    assert len(sells) == 10
    for s in sells:
        assert s["confidence"] == 0.6
        assert s["strategy_name"] == "quality_factor"
