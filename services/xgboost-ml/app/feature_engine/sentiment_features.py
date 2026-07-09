"""
Sentiment Features
Extracts features from sentiment analysis data stored in PostgreSQL.
"""

import logging
import numpy as np
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SentimentFeatures:
    """Features derived from news sentiment and community data."""

    def get_aggregate_sentiment(self, sentiment_data: List[Dict]) -> Dict:
        """Calculate aggregate sentiment features from time-series sentiment data."""
        features = {
            "sentiment_avg": 0.0, "sentiment_avg_5d": 0.0,
            "sentiment_avg_20d": 0.0, "sentiment_trend": 0.0,
            "sentiment_volatility": 0.0,
            "news_count_5d": 0, "news_count_20d": 0,
            "authenticity_avg": 0.0,
            "positive_ratio": 0.0, "negative_ratio": 0.0,
        }

        if not sentiment_data:
            return features

        scores = [s.get("avg_sentiment", 0) for s in sentiment_data]
        counts = [s.get("sentiment_count", 0) for s in sentiment_data]
        pos_counts = [s.get("positive_count", 0) for s in sentiment_data]
        neg_counts = [s.get("negative_count", 0) for s in sentiment_data]
        auth_scores = [s.get("avg_authenticity", 0) for s in sentiment_data]

        features["sentiment_avg"] = float(np.mean(scores)) if scores else 0.0

        if len(scores) >= 5:
            features["sentiment_avg_5d"] = float(np.mean(scores[-5:]))
        else:
            features["sentiment_avg_5d"] = features["sentiment_avg"]

        if len(scores) >= 20:
            features["sentiment_avg_20d"] = float(np.mean(scores[-20:]))
        else:
            features["sentiment_avg_20d"] = features["sentiment_avg"]

        features["sentiment_trend"] = float(scores[-1] - scores[0]) if len(scores) >= 2 else 0.0
        features["sentiment_volatility"] = float(np.std(scores)) if len(scores) > 1 else 0.0

        features["news_count_5d"] = int(np.sum(counts[-5:])) if len(counts) >= 5 else int(np.sum(counts))
        features["news_count_20d"] = int(np.sum(counts[-20:])) if len(counts) >= 20 else int(np.sum(counts))

        features["authenticity_avg"] = float(np.mean(auth_scores)) if auth_scores else 0.0

        total_pos = int(np.sum(pos_counts))
        total_neg = int(np.sum(neg_counts))
        total_all = total_pos + total_neg + int(np.sum(
            [n.get("neutral_count", 0) for n in sentiment_data]
        ))

        features["positive_ratio"] = float(total_pos / total_all) if total_all else 0.0
        features["negative_ratio"] = float(total_neg / total_all) if total_all else 0.0

        return features

    def get_disclosure_count(self, stock_code: str, db_conn=None) -> Dict:
        """Count recent disclosures for a stock."""
        features = {"disclosure_count_5d": 0}

        if db_conn is None:
            return features

        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM news_analysis
                WHERE source = 'DART'
                AND analyzed_at >= CURRENT_DATE - INTERVAL '5 days'
            """)
            row = cur.fetchone()
            cur.close()
            features["disclosure_count_5d"] = int(row[0]) if row else 0
        except Exception as e:
            logger.debug(f"Disclosure count failed: {e}")

        return features

    def get_sentiment_from_db(
        self, stock_code: str, db_conn=None, days: int = 20
    ) -> List[Dict]:
        """Fetch sentiment time-series from PostgreSQL."""
        if db_conn is None:
            return []

        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT analysis_date, avg_sentiment, sentiment_count,
                       positive_count, negative_count, neutral_count,
                       avg_authenticity
                FROM stock_sentiment
                WHERE stock_code = %s
                ORDER BY analysis_date DESC
                LIMIT %s
            """, (stock_code, days))
            rows = cur.fetchall()
            cur.close()

            return [{
                "avg_sentiment": float(r[1]) if r[1] else 0,
                "sentiment_count": int(r[2]) if r[2] else 0,
                "positive_count": int(r[3]) if r[3] else 0,
                "negative_count": int(r[4]) if r[4] else 0,
                "neutral_count": int(r[5]) if r[5] else 0,
                "avg_authenticity": float(r[6]) if r[6] else 0,
            } for r in rows]

        except Exception as e:
            logger.debug(f"Sentiment DB fetch failed for {stock_code}: {e}")
            return []

    def get_all_features(self, stock_code: str, db_conn=None) -> Dict:
        """Get all sentiment-based features."""
        features = {}
        sentiment_data = self.get_sentiment_from_db(stock_code, db_conn)
        features.update(self.get_aggregate_sentiment(sentiment_data))
        features.update(self.get_disclosure_count(stock_code, db_conn))
        return features
