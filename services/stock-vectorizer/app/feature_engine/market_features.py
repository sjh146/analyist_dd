"""
Market Feature Extractor
Extracts market-level features for vectorization.
"""

import numpy as np


class MarketFeatures:
    """Extract market-level features."""

    @staticmethod
    def get_market_cap_feature(market_cap: int) -> float:
        """Normalized log market cap."""
        return np.log1p(market_cap) / 30.0 if market_cap > 0 else 0

    @staticmethod
    def get_sector_feature(sector: str) -> list:
        """One-hot encoding for sector."""
        sectors = [
            "반도체", "자동차", "금융", "바이오", "IT",
            "에너지", "경기소비", "소재", "2차전지", "게임",
        ]
        return [1.0 if sector == s else 0.0 for s in sectors]

    @staticmethod
    def get_market_type(market: str) -> list:
        """Market type encoding."""
        return [1.0, 0.0] if market == "KOSPI" else [0.0, 1.0]
