"""
Price Feature Extractor
Extracts numerical price features for vectorization.
"""

import numpy as np
import pandas as pd


class PriceFeatures:
    """Extract price-related features."""

    @staticmethod
    def get_returns_features(df: pd.DataFrame) -> dict:
        """Calculate return-based features."""
        if df.empty:
            return {}

        close = df["close"].values
        features = {}

        # Daily returns
        returns = np.diff(close) / close[:-1]
        features["return_1d"] = returns[-1] if len(returns) > 0 else 0
        features["return_5d"] = np.sum(returns[-5:]) if len(returns) >= 5 else 0
        features["return_20d"] = np.sum(returns[-20:]) if len(returns) >= 20 else 0
        features["return_60d"] = np.sum(returns[-60:]) if len(returns) >= 60 else 0

        # Risk-adjusted return
        if len(returns) > 1 and np.std(returns) > 0:
            features["sharpe_20d"] = np.mean(returns[-20:]) / np.std(returns[-20:]) * np.sqrt(252)
        else:
            features["sharpe_20d"] = 0

        return features

    @staticmethod
    def get_volatility_features(df: pd.DataFrame) -> dict:
        """Calculate volatility features."""
        if df.empty:
            return {}

        close = df["close"].values
        returns = np.diff(close) / close[:-1]

        features = {}
        features["volatility_5d"] = np.std(returns[-5:]) if len(returns) >= 5 else 0
        features["volatility_20d"] = np.std(returns[-20:]) if len(returns) >= 20 else 0
        features["volatility_60d"] = np.std(returns[-60:]) if len(returns) >= 60 else 0

        features["atr"] = np.mean(
            df["high"].values - df["low"].values
        ) if not df.empty else 0

        return features

    @staticmethod
    def get_momentum_features(df: pd.DataFrame) -> dict:
        """Calculate momentum features."""
        if df.empty:
            return {}

        close = df["close"].values
        features = {}

        # Rate of change
        features["roc_5d"] = (close[-1] / close[-5] - 1) if len(close) >= 5 else 0
        features["roc_20d"] = (close[-1] / close[-20] - 1) if len(close) >= 20 else 0

        # Moving average cross
        ma5 = np.mean(close[-5:]) if len(close) >= 5 else close[-1]
        ma20 = np.mean(close[-20:]) if len(close) >= 20 else close[-1]
        features["ma_cross"] = (ma5 - ma20) / ma20 if ma20 > 0 else 0

        return features
