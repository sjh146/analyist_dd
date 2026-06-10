"""
Vector Features
Extracts features from pgvector similarity search.
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


class VectorFeatures:
    """Features derived from vector similarity."""

    def get_similar_stock_features(self, stock_code: str, storage) -> Dict:
        """Get features from similar stocks."""
        features = {}
        try:
            similar = storage.find_similar_stocks(stock_code, top_k=10)
            if similar:
                similarities = [s.get("similarity", 0) for s in similar]
                features["avg_similarity"] = float(np.mean(similarities))
                features["max_similarity"] = float(np.max(similarities))
                features["similarity_std"] = float(np.std(similarities))
                features["similar_count"] = len(similar)
            else:
                features.update({"avg_similarity": 0, "max_similarity": 0,
                                 "similarity_std": 0, "similar_count": 0})
        except Exception as e:
            logger.debug(f"Vector features failed: {e}")
            features.update({"avg_similarity": 0, "max_similarity": 0,
                             "similarity_std": 0, "similar_count": 0})
        return features
