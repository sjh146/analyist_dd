"""
Technical Indicator Calculator
Calculates common technical indicators from price data.
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class TechnicalIndicatorCalculator:
    """Calculates technical indicators for stock data."""

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all technical indicators."""
        try:
            df = self.calculate_moving_averages(df)
            df = self.calculate_rsi(df)
            df = self.calculate_macd(df)
            df = self.calculate_bollinger_bands(df)
            df = self.calculate_volume_indicators(df)
            return df
        except Exception as e:
            logger.error(f"Failed to calculate indicators: {e}")
            return df

    def calculate_moving_averages(self, df: pd.DataFrame, windows: list = None) -> pd.DataFrame:
        """Calculate moving averages."""
        if windows is None:
            windows = [5, 10, 20, 60]

        for w in windows:
            df[f"ma_{w}"] = df.groupby("stock_code")["close"].transform(
                lambda x: x.rolling(window=w).mean()
            )
        return df

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Calculate Relative Strength Index."""
        def _rsi(series):
            delta = series.diff()
            gain = delta.where(delta > 0, 0).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            return 100 - (100 / (1 + rs))

        df["rsi"] = df.groupby("stock_code")["close"].transform(_rsi)
        return df

    def calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate MACD (Moving Average Convergence Divergence)."""
        def _macd(series):
            exp1 = series.ewm(span=12, adjust=False).mean()
            exp2 = series.ewm(span=26, adjust=False).mean()
            macd_line = exp1 - exp2
            signal = macd_line.ewm(span=9, adjust=False).mean()
            return pd.DataFrame({
                "macd": macd_line,
                "macd_signal": signal,
                "macd_hist": macd_line - signal,
            })

        result = df.groupby("stock_code")["close"].apply(_macd)
        # Flatten the result
        for col in ["macd", "macd_signal", "macd_hist"]:
            df[col] = result.xs(col, level=-1)

        return df

    def calculate_bollinger_bands(
        self, df: pd.DataFrame, period: int = 20, std_dev: int = 2
    ) -> pd.DataFrame:
        """Calculate Bollinger Bands."""
        def _bb(series):
            ma = series.rolling(window=period).mean()
            std = series.rolling(window=period).std()
            return pd.DataFrame({
                "bb_middle": ma,
                "bb_upper": ma + (std * std_dev),
                "bb_lower": ma - (std * std_dev),
                "bb_width": ((ma + (std * std_dev)) - (ma - (std * std_dev))) / ma * 100,
            })

        result = df.groupby("stock_code")["close"].apply(_bb)
        for col in ["bb_middle", "bb_upper", "bb_lower", "bb_width"]:
            df[col] = result.xs(col, level=-1)

        return df

    def calculate_volume_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate volume-based indicators."""
        # Volume moving average
        df["volume_ma_20"] = df.groupby("stock_code")["volume"].transform(
            lambda x: x.rolling(window=20).mean()
        )
        # Volume ratio
        df["volume_ratio"] = df["volume"] / df["volume_ma_20"]
        # VWAP (approximate)
        df["vwap"] = (df["volume"] * df["close"]).groupby(df["stock_code"]).transform(
            lambda x: x.rolling(window=20).sum()
        ) / df["volume_ma_20"]

        return df
