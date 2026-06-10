"""
Sentiment Features
Extracts features from sentiment analysis data.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class SentimentFeatures:
    """Features derived from sentiment data."""

    def get_sentiment_features(self, sentiment_data: List[Dict]) -> Dict:
        """Calculate sentiment-based features."""
        features = {}

        if not sentiment_data:
            features.update({
                "sentiment_avg": 0, "sentiment_trend": 0,
                "sentiment_volatility": 0, "news_count": 0,
            })
            return features

        scores = [s.get("avg_sentiment", 0) for s in sentiment_data]

        features["sentiment_avg"] = float(np.mean(scores)) if scores else 0
        features["sentiment_trend"] = float(scores[-1] - scores[0]) if len(scores) >= 2 else 0
        features["sentiment_volatility"] = float(np.std(scores)) if len(scores) > 1 else 0
        features["news_count"] = sum(s.get("sentiment_count", 0) for s in sentiment_data)

        return features
