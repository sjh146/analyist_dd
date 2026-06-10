"""
Combined Vectorizer
Creates comprehensive stock embeddings by combining all vector types.
"""

import numpy as np
from typing import Optional
from app.vectorizers.price_vectorizer import PriceVectorizer
from app.vectorizers.fundamental_vectorizer import FundamentalVectorizer
from app.vectorizers.sentiment_vectorizer import SentimentVectorizer


class CombinedVectorizer:
    """Creates comprehensive stock vectors combining all data sources."""

    def __init__(self, combined_dim: int = 1024):
        self.combined_dim = combined_dim
        self.price_vectorizer = PriceVectorizer(vector_dim=256)
        self.fundamental_vectorizer = FundamentalVectorizer(vector_dim=256)
        self.sentiment_vectorizer = SentimentVectorizer(vector_dim=256)

    def vectorize_price_pattern(self, stock_code: str) -> np.ndarray:
        """Vectorize price pattern (standalone)."""
        return self.price_vectorizer.vectorize(
            close_prices=None
        )  # Will be called with actual data from storage

    def vectorize_sentiment(self, stock_code: str) -> np.ndarray:
        """Vectorize sentiment (standalone)."""
        return self.sentiment_vectorizer.vectorize([])

    def vectorize_fundamentals(self, stock_data: dict) -> np.ndarray:
        """Vectorize fundamentals (standalone)."""
        return self.fundamental_vectorizer.vectorize(stock_data)

    def create_combined_embedding(
        self,
        price_vector: np.ndarray,
        sentiment_vector: np.ndarray,
        fundamental_vector: np.ndarray,
    ) -> np.ndarray:
        """
        Combine multiple vectors into final embedding.
        
        Args:
            price_vector: 256-d price pattern vector
            sentiment_vector: 256-d sentiment vector
            fundamental_vector: 256-d fundamental vector
        
        Returns:
            Combined 1024-d vector
        """
        # Concatenate all vectors
        combined = np.concatenate([
            price_vector,
            sentiment_vector,
            fundamental_vector,
        ])

        # Add statistical summary features
        stats = np.array([
            np.mean(combined),
            np.std(combined),
            np.max(combined),
            np.min(combined),
        ])
        combined = np.concatenate([combined, stats])

        # Pad to target dimension if needed
        if len(combined) < self.combined_dim:
            combined = np.pad(combined, (0, self.combined_dim - len(combined)))
        elif len(combined) > self.combined_dim:
            combined = combined[:self.combined_dim]

        # Normalize to unit length
        norm = np.linalg.norm(combined)
        if norm > 0:
            combined = combined / norm

        return combined
