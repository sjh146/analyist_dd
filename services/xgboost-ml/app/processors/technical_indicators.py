"""Technical indicator calculations (SMA, RSI, MACD, ATR).

Self-contained pandas-based calculator used by the feature pipeline tests and
available to feature engineering. Mirrors the interface the T6 integration test
expects: ``TechnicalIndicatorCalculator().calculate_all(df)`` returns the input
DataFrame plus ``sma_20``, ``rsi``, ``macd`` and ``atr`` columns.

Indicators
----------
- ``sma_20``: 20-period simple moving average of close.
- ``rsi``  : 14-period relative strength index (Wilder smoothing via EMA).
- ``macd`` : MACD line (12-EMA minus 26-EMA).
- ``atr``  : 14-period average true range (Wilder smoothing).
"""

from typing import Optional

import numpy as np
import pandas as pd


class TechnicalIndicatorCalculator:
    """Compute a standard set of technical indicators from OHLCV data."""

    def __init__(self, rsi_period: int = 14, atr_period: int = 14) -> None:
        self.rsi_period = rsi_period
        self.atr_period = atr_period

    @staticmethod
    def _sma(close: pd.Series, window: int = 20) -> pd.Series:
        return close.rolling(window=window, min_periods=window).mean()

    def _rsi(self, close: pd.Series) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1.0 / self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / self.rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        rsi = 100.0 - 100.0 / (1.0 + rs)
        return rsi

    @staticmethod
    def _macd(close: pd.Series) -> pd.Series:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        return ema12 - ema26

    def _atr(self, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.ewm(alpha=1.0 / self.atr_period, adjust=False).mean()

    def calculate_all(
        self,
        df: pd.DataFrame,
        close_col: str = "close",
        high_col: str = "high",
        low_col: str = "low",
    ) -> pd.DataFrame:
        """Return ``df`` plus indicator columns (NaN where not enough history)."""
        result = df.copy()
        for col in (close_col, high_col, low_col):
            if col not in result.columns:
                raise ValueError(f"Input DataFrame must contain a '{col}' column")

        close = result[close_col].astype(float)
        high = result[high_col].astype(float)
        low = result[low_col].astype(float)

        result["sma_20"] = self._sma(close)
        result["rsi"] = self._rsi(close)
        result["macd"] = self._macd(close)
        result["atr"] = self._atr(high, low, close)
        return result
