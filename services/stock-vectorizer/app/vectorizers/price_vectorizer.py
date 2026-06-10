"""
Price Pattern Vectorizer
Converts price history into vector embeddings.
"""

import numpy as np
import pandas as pd
from typing import Optional
from sklearn.preprocessing import StandardScaler


class PriceVectorizer:
    """Vectorize price patterns for similarity search."""

    def __init__(self, vector_dim: int = 256):
        self.vector_dim = vector_dim
        self.scaler = StandardScaler()

    def vectorize(self, close_prices: pd.Series, volumes: Optional[pd.Series] = None) -> np.ndarray:
        """
        Convert price history to vector embedding.
        
        Args:
            close_prices: Series of closing prices
            volumes: Optional series of volumes
        
        Returns:
            Normalized vector embedding
        """
        if close_prices is None or len(close_prices) < 20:
            # Return zero vector if insufficient data
            return np.zeros(self.vector_dim)

        prices = close_prices.values[-60:]  # Last 60 days
        features = []

        # 1. Returns (daily, weekly, monthly)
        returns_1d = np.diff(prices) / prices[:-1]
        features.extend([
            np.mean(returns_1d),
            np.std(returns_1d),
            np.max(returns_1d),
            np.min(returns_1d),
            returns_1d[-1] if len(returns_1d) > 0 else 0,
        ])

        # 2. Moving averages cross
        ma5 = np.mean(prices[-5:]) if len(prices) >= 5 else prices[-1]
        ma20 = np.mean(prices[-20:]) if len(prices) >= 20 else prices[-1]
        ma60 = np.mean(prices) if len(prices) >= 60 else prices[-1]
        features.extend([ma5 / prices[-1], ma20 / prices[-1], ma60 / prices[-1]])

        # 3. Volatility
        features.append(np.std(returns_1d))

        # 4. Price position (where is current price relative to range)
        price_range = np.max(prices) - np.min(prices)
        if price_range > 0:
            features.append((prices[-1] - np.min(prices)) / price_range)
        else:
            features.append(0.5)

        # 5. Trend strength
        from sklearn.linear_model import LinearRegression
        if len(prices) >= 20:
            X = np.arange(len(prices)).reshape(-1, 1)
            model = LinearRegression().fit(X, prices)
            features.append(model.coef_[0])  # Slope
            features.append(model.score(X, prices))  # R-squared
        else:
            features.extend([0, 0])

        # 6. Normalized price series (resampled)
        target_len = self.vector_dim - len(features)
        if len(prices) >= target_len:
            indices = np.linspace(0, len(prices) - 1, target_len, dtype=int)
            price_norm = prices[indices]
        else:
            price_norm = np.pad(prices, (0, target_len - len(prices)), 'edge')

        price_norm = (price_norm - np.min(price_norm)) / (
            np.max(price_norm) - np.min(price_norm) + 1e-8
        )

        # 7. Combine features
        features = np.array(features)
        features = np.clip(features, -10, 10)  # Clip outliers

        embedding = np.concatenate([price_norm, features])

        # 8. Normalize to unit length
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding
