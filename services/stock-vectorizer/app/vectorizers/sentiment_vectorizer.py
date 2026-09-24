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

        # 실측 수정(2026-09-24): psycopg2 는 numeric 컬럼을 Decimal 로 돌려준다.
        # 그대로 np.concatenate/np.linalg.norm 에 넣으면
        # "unsupported operand type(s) for +: 'decimal.Decimal' and 'float'" 로 죽어
        # **임베딩이 저장되지 않았다**(실측: 재생성 20종목 중 14종목 실패).
        scores = [float(s.get("avg_sentiment") or 0.0) for s in sentiment_data]
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
