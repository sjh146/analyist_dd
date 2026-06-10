"""
Fundamental Vectorizer
Creates embeddings from fundamental stock data.
"""

import numpy as np
from typing import Dict


class FundamentalVectorizer:
    """Vectorize fundamental data for stocks."""

    def __init__(self, vector_dim: int = 256):
        self.vector_dim = vector_dim

    def vectorize(self, stock_data: Dict) -> np.ndarray:
        """
        Create fundamental embedding from stock data.
        
        Args:
            stock_data: Dict with stock info (sector, industry, market cap, etc.)
        
        Returns:
            Fundamental vector embedding
        """
        features = []

        # 1. Market cap (log normalized)
        market_cap = stock_data.get("market_cap", 0)
        features.append(np.log1p(market_cap) / 30.0)  # Normalize

        # 2. Sector one-hot-like encoding via hashing
        sector = stock_data.get("sector", "Unknown")
        sector_hash = hash(sector) % 50
        sector_encoding = np.zeros(50)
        sector_encoding[sector_hash] = 1.0
        features.extend(sector_encoding.tolist())

        # 3. Market type
        market = stock_data.get("market", "KOSPI")
        features.extend([1.0 if market == "KOSPI" else 0.0,
                         1.0 if market == "KOSDAQ" else 0.0])

        # 4. Per ratio features if available
        ratios = []
        for key in ["per", "pbr", "eps", "roe", "dividend_yield"]:
            val = stock_data.get(key, 0)
            ratios.append(np.tanh(val / 100))  # Normalize with tanh

        features.extend(ratios)

        # 5. Pad to vector_dim
        embedding = np.array(features)
        if len(embedding) < self.vector_dim:
            embedding = np.pad(embedding, (0, self.vector_dim - len(embedding)))
        else:
            embedding = embedding[:self.vector_dim]

        # Normalize
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding
