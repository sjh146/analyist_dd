"""
Fundamental Vectorizer
Creates embeddings from fundamental stock data.
"""

import hashlib

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
        # 실측 수정(2026-09-24): 파이썬 내장 hash() 는 프로세스마다 솔트가 달라져
        # (PYTHONHASHSEED 무작위) 재실행할 때마다 같은 섹터가 다른 버킷에 들어갔다.
        # 임베딩이 실행마다 달라지면 유사도 피처가 임의 노이즈가 되므로 안정 해시를 쓴다.
        sector = stock_data.get("sector") or "Unknown"
        sector_hash = int(hashlib.md5(str(sector).encode("utf-8")).hexdigest(), 16) % 50
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
