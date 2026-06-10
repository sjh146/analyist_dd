"""
Sentiment Vectorizer
Creates embeddings from sentiment data.
"""

import numpy as np
from typing import Optional


class SentimentVectorizer:
    """Vectorize sentiment data for stocks."""

    def __init__(self, vector_dim: int = 256):
        self.vector_dim = vector_dim

    def vectorize(self, sentiment_data: list) -> np.ndarray:
        """
        Create sentiment embedding from historical sentiment data.
        
        Args:
            sentiment_data: List of sentiment records with score and date
        
        Returns:
            Sentiment vector embedding
        """
        if not sentiment_data:
            return np.zeros(self.vector_dim)

        scores = [s.get("avg_sentiment", 0) for s in sentiment_data]
        if not scores:
            return np.zeros(self.vector_dim)

        features = []

        # 1. Recent sentiment (last 5 days)
        recent = scores[-5:] if len(scores) >= 5 else scores
        features.append(np.mean(recent))
        features.append(np.std(recent) if len(recent) > 1 else 0)

        # 2. Medium-term trend (last 20 days)
        midterm = scores[-20:] if len(scores) >= 20 else scores
        features.append(np.mean(midterm))
        features.append(np.std(midterm) if len(midterm) > 1 else 0)

        # 3. Sentiment momentum (change over last 3 days)
        if len(scores) >= 4:
            momentum = scores[-1] - scores[-4]
        else:
            momentum = scores[-1] - scores[0] if len(scores) >= 2 else 0
        features.append(momentum)

        # 4. Sentiment volatility
        features.append(np.std(scores) if len(scores) > 1 else 0)

        # 5. Positive/negative ratio
        positive = sum(1 for s in scores if s > 0.2)
        negative = sum(1 for s in scores if s < -0.2)
        total = len(scores)
        features.append(positive / total if total > 0 else 0)
        features.append(negative / total if total > 0 else 0)

        # 6. Sentiment distribution (resampled)
        if len(scores) > self.vector_dim - len(features):
            indices = np.linspace(0, len(scores) - 1, self.vector_dim - len(features), dtype=int)
            sent_norm = np.array([scores[i] for i in indices])
        else:
            sent_norm = np.pad(scores, (0, self.vector_dim - len(features) - len(scores)), 'edge')

        embedding = np.concatenate([features, sent_norm])

        # Normalize
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding
