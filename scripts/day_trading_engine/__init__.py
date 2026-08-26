"""Day-trading screener engine (day_trading_engine package)."""

from .providers import (DbDailyProvider, FixtureProvider, MarketDataProvider,
                        OHLCV_COLUMNS, StockInfo)
from .pipeline import (ChampionPredictor, OUTPUT_COLUMNS, run_screener)
from .scoring import (compute_kalman_features, filter_candidates,
                      rank_candidates, score_candidates)
from .minute_provider import (DEFAULT_WINDOW_MINUTES, DailyGapProvider,
                              KisMinuteProvider, MinutePriceProvider)

__all__ = [
    "MarketDataProvider", "DbDailyProvider", "FixtureProvider", "StockInfo",
    "OHLCV_COLUMNS", "ChampionPredictor", "run_screener", "OUTPUT_COLUMNS",
    "compute_kalman_features", "score_candidates", "filter_candidates",
    "rank_candidates",
    # minute-price window scoring (30-min window / gap proxy)
    "MinutePriceProvider", "DailyGapProvider", "KisMinuteProvider",
    "DEFAULT_WINDOW_MINUTES",
]
