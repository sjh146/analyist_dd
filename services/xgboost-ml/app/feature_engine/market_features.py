"""
Market Features
Extracts features from market data.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class MarketFeatures:
    """Features derived from price/volume data."""

    def get_price_features(self, df: pd.DataFrame) -> Dict:
        """Calculate price-based features."""
        if df.empty:
            return {}

        close = df["close_price"].values if "close_price" in df.columns else df["close"].values
        volume = df["volume"].values if "volume" in df.columns else np.array([])

        features = {}
        features["price"] = close[-1] if len(close) > 0 else 0
        features["return_1d"] = (close[-1] / close[-2] - 1) if len(close) >= 2 else 0
        features["return_5d"] = (close[-1] / close[-5] - 1) if len(close) >= 5 else 0
        features["return_20d"] = (close[-1] / close[-20] - 1) if len(close) >= 20 else 0
        features["volatility_20d"] = float(np.std(
            [close[i] / close[i-1] - 1 for i in range(1, len(close))][-20:]
        )) if len(close) >= 21 else 0
        return features

    def get_technical_features(self, df: pd.DataFrame) -> Dict:
        """Calculate technical indicator features."""
        features = {}
        if "rsi" in df.columns:
            features["rsi"] = df["rsi"].values[-1] if len(df) > 0 else 50
        if "macd" in df.columns:
            features["macd"] = df["macd"].values[-1] if len(df) > 0 else 0
        if "bb_width" in df.columns:
            features["bb_width"] = df["bb_width"].values[-1] if len(df) > 0 else 0
        return features
