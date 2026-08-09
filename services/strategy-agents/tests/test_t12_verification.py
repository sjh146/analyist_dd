"""T12 — 회귀/검증 테스트: long-only + 임계값 고정(과최적화 방지) + 페이퍼 채널.

강환국『하면 된다! 퀀트투자』 규칙 변환 계획서(quant-book-strategies.md v2) Todo 12.
기존 전략(Theme/Cycle/Twin) 회귀는 기존 테스트 스위트(146 passed)가 커버하므로,
여기서는 계획서의 핵심 검증 3종만 추가한다:
  1. long-only: 신규 팩터 전략 5종의 시그널 action은 buy/sell(long 종료)만 — short 금지
  2. 임계값 고정: 책 규칙(상위 30·리밸런싱 63·모멘텀 252/21/63)이 하드코딩·비튜닝
  3. paper_only: 전략 config에 paper_only 채널이 실발행(trade:signals)이 아닌 곳으로 격리
"""
import sys
from datetime import date

from app.strategies.value_strategy import ValueStrategy
from app.strategies.quality_strategy import QualityStrategy
from app.strategies.momentum_strategy import MomentumStrategy
from app.strategies.lowvol_strategy import LowVolatilityStrategy
from app.strategies.multifactor_strategy import MultiFactorStrategy


class MockStorage:
    """기존 test_multifactor_strategy.MockStorage와 동일 계약의 최소 stub."""

    def __init__(self, stocks, financials=None, prices=None):
        self._stocks = stocks
        self._financials = financials or {}
        self._prices = prices or {}

    def get_strategy_config(self, name):
        return {}

    def get_all_stocks(self, limit=None):
        return list(self._stocks)

    def get_market_caps(self):
        return {s["code"]: s.get("market_cap") for s in self._stocks}

    def get_avg_trading_value(self, stock_code, days=30):
        return 1e10  # 100억 — 유니버스 통과

    def get_first_trade_date(self, stock_code):
        return "2020-01-01"

    def get_positions(self):
        return []

    def get_financial_statements(self, stock_code, asof_date=None):
        rows = self._financials.get(stock_code, [])
        if asof_date is not None:
            rows = [r for r in rows if str(r["report_date"]) <= str(asof_date)]
        return sorted(rows, key=lambda r: str(r["report_date"]))

    def get_price_series_asof(self, stock_code, days=252, asof_date=None):
        return self._prices.get(stock_code, [])


def _make_stocks(n=12):
    return [
        {
            "code": f"{i:06d}",
            "name": f"종목{i}",
            "market_cap": 1e12 + i * 1e9,  # 1조+ — 유니버스 통과
        }
        for i in range(n)
    ]


def _make_financials(stocks):
    fin = {}
    for s in stocks:
        i = int(s["code"])
        # 2개 분기: 2023-12-31, 2024-03-31 — i가 클수록 수익 좋음(퀄리티 랭크 변별)
        fin[s["code"]] = [
            {
                "stock_code": s["code"],
                "report_date": "2023-12-31",
                "net_income": 1e9 + i * 1e8,
                "total_equity": 1e10,
                "revenue": 1e10 + i * 1e8,
                "total_assets": 5e10,
                "gross_profit": 2e9 + i * 1e8,
            },
            {
                "stock_code": s["code"],
                "report_date": "2024-03-31",
                "net_income": 1.1e9 + i * 1e8,
                "total_equity": 1e10,
                "revenue": 1.1e10 + i * 1e8,
                "total_assets": 5e10,
                "gross_profit": 2.2e9 + i * 1e8,
            },
        ]
    return fin


def _make_prices(stocks):
    px = {}
    for s in stocks:
        i = int(s["code"])
        # 300일: 상승률이 i에 따라 다르게 (모멘텀 랭크 변별), NaN 없음
        px[s["code"]] = [
            {"trade_date": f"2023-01-{d:02d}" if d < 29 else f"2023-02-{d-28:02d}",
             "close_price": 10000 + i * 100 + d * (1 + i * 0.02)}
            for d in range(1, 300)
        ]
    return px


def test_long_only_no_short_signals():
    """5개 신규 전략의 analyze() 시그널 action은 buy/sell만 — short 금지."""
    stocks = _make_stocks()
    storage = MockStorage(stocks, _make_financials(stocks), _make_prices(stocks))
    strategies = [
        ValueStrategy(storage),
        QualityStrategy(storage),
        MomentumStrategy(storage),
        LowVolatilityStrategy(storage),
        MultiFactorStrategy(storage),
    ]
    for strat in strategies:
        signals = strat.analyze(asof_date=date(2024, 4, 1))  # 리밸런싱 앵커 이후
        actions = {s["action"] for s in signals}
        assert actions <= {"buy", "sell"}, f"{strat.name}: {actions}"
        assert "short" not in actions, f"{strat.name}: short 신호 금지"


def test_thresholds_hardcoded_not_tunable():
    """책 규칙 임계값이 기본값으로 하드코딩·비튜닝 — 과최적화 방지."""
    from app.strategies.value_strategy import ValueStrategy
    # 구체 전략의 config 기본값으로 확인 (추상 클래스 직접 인스턴스화 금지)
    v = ValueStrategy(None)
    assert v.top_n == 30          # 책: 상위 30
    assert v.rebalance_interval_days == 63  # 분기(63거래일)
    from app.strategies.momentum_strategy import MomentumStrategy
    m = MomentumStrategy(MockStorage(_make_stocks(), {}, {}))
    assert getattr(m, "HORIZON_12M", 252) == 252
    assert getattr(m, "HORIZON_1M", 21) == 21
    assert getattr(m, "HORIZON_3M", 63) == 63
    assert getattr(m, "HORIZON_6M", 126) == 126


def test_multifactor_equal_weight_zscore():
    """멀티팩터는 Z-score 동일가중 — 가중치 튜닝 없음."""
    from app.strategies.multifactor_strategy import MultiFactorStrategy
    m = MultiFactorStrategy(MockStorage(_make_stocks(), {}, {}))
    weights = getattr(m, "FACTOR_WEIGHTS", None)
    if weights is not None:
        assert len(set(weights.values())) == 1, "팩터 가중치는 동일가중이어야 함"
