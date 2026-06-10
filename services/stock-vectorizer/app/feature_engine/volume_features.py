"""
Volume Feature Extractor
Extracts volume-related features for vectorization.
"""

import numpy as np
import pandas as pd


class VolumeFeatures:
    """Extract volume-related features."""

    @staticmethod
    def get_volume_trend(df: pd.DataFrame) -> dict:
        """Calculate volume trend features."""
        if df.empty:
            return {}

        volume = df["volume"].values
        features = {}

        if len(volume) >= 20:
            features["volume_ma_5"] = np.mean(volume[-5:])
            features["volume_ma_20"] = np.mean(volume[-20:])
            features["volume_ratio"] = volume[-1] / (features["volume_ma_20"] + 1)
        else:
            features["volume_ratio"] = 1.0

        return features

    @staticmethod
    def get_volume_price_correlation(df: pd.DataFrame) -> dict:
        """Calculate volume-price correlation."""
        if df.empty or len(df) < 10:
            return {}

        volume = df["volume"].values[-20:]
        close = df["close"].values[-20:]

        if len(volume) > 1 and np.std(volume) > 0 and np.std(close) > 0:
            corr = np.corrcoef(volume, close)[0, 1]
            return {"volume_price_corr": corr}
        return {"volume_price_corr": 0}
