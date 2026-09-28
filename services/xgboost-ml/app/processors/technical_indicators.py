"""Technical indicator calculations (SMA, RSI, MACD, ATR, 볼린저, 스토캐스틱).

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
- ``bb_middle``/``bb_upper``/``bb_lower``/``bb_width``/``bb_position`` : 볼린저(20, 2σ).
- ``stoch_k``/``stoch_d`` : 스토캐스틱(14, 3).

왜 볼린저·스토캐스틱을 추가했나 (2026-09-28 실측):
    ``feature_pipeline`` 의 기술 피처 목록은 `rsi, macd, bb_width, bb_position, atr, atr_pct,
    stoch_k, stoch_d` 8개인데 이 계산기는 sma_20/rsi/macd/atr 만 만들었다. 그래서
    `market_features.get_technical_features` 가 bb_*/stoch_* 를 못 채워 기본값으로 떨어졌고,
    피처 커버리지에서 **bb_position·bb_width·stoch_k·stoch_d 의 nonzero_ratio 가 0** 인
    '죽은 피처'로 남아 있었다(market_features 주석에 그 진단이 그대로 적혀 있다).
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

    @staticmethod
    def _bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0):
        """볼린저 밴드(20, 2σ) — (middle, upper, lower, width%, position) 를 돌려준다.

        ``min_periods=window`` 로 두어 SMA(20) 과 워밍업을 맞춘다(부족하면 NaN → 피처 빌더가
        NaN 처리를 하도록 남겨 둔다. 0 으로 채우면 상수 피처가 되어 '죽은 피처'가 된다).
        """
        mid = close.rolling(window=window, min_periods=window).mean()
        std = close.rolling(window=window, min_periods=window).std()
        upper = mid + num_std * std
        lower = mid - num_std * std
        span = (upper - lower).replace(0.0, np.nan)          # 밴드 폭 0 (변동성 없음) → NaN
        width = (span / mid.replace(0.0, np.nan)) * 100.0
        position = (close - lower) / span                    # 0=하단, 1=상단
        return mid, upper, lower, width, position

    @staticmethod
    def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                    k_period: int = 14, d_period: int = 3):
        """스토캐스틱(14, 3, 3) — (slow_k, slow_d)."""
        low_min = low.rolling(window=k_period, min_periods=k_period).min()
        high_max = high.rolling(window=k_period, min_periods=k_period).max()
        span = (high_max - low_min).replace(0.0, np.nan)
        fast_k = ((close - low_min) / span) * 100.0
        slow_k = fast_k.rolling(window=d_period, min_periods=1).mean()
        slow_d = slow_k.rolling(window=d_period, min_periods=1).mean()
        return slow_k, slow_d

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

        mid, upper, lower, width, position = self._bollinger(close)
        result["bb_middle"] = mid
        result["bb_upper"] = upper
        result["bb_lower"] = lower
        result["bb_width"] = width
        result["bb_position"] = position

        slow_k, slow_d = self._stochastic(high, low, close)
        result["stoch_k"] = slow_k
        result["stoch_d"] = slow_d
        return result
