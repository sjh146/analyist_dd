"""
Vector Features
Extracts features from pgvector similarity search.
"""

import logging
import numpy as np
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class VectorFeatures:
    """Features derived from pgvector stock similarity search."""

    def get_similar_stock_features(
        self, stock_code: str, db_conn=None, top_k: int = 10
    ) -> Dict:
        """Get features from top-K similar stocks via pgvector cosine similarity."""
        features = {
            "avg_similarity_top10": 0.0, "max_similarity": 0.0,
            "similarity_std": 0.0, "similar_count": 0,
            "similar_stocks_return_avg": 0.0, "similar_stocks_return_std": 0.0,
        }

        if db_conn is None:
            return features

        try:
            similar = self._find_similar_stocks(stock_code, db_conn, top_k)
            if not similar:
                return features

            similarities = [s.get("similarity", 0) for s in similar]
            features["avg_similarity_top10"] = float(np.mean(similarities))
            features["max_similarity"] = float(np.max(similarities))
            features["similarity_std"] = float(np.std(similarities)) if len(similarities) > 1 else 0.0
            features["similar_count"] = len(similar)

            returns = [
                s.get("return_5d", 0)
                for s in similar
                if s.get("return_5d") is not None
            ]
            if returns:
                features["similar_stocks_return_avg"] = float(np.mean(returns))
                features["similar_stocks_return_std"] = float(np.std(returns)) if len(returns) > 1 else 0.0

        except Exception as e:
            logger.debug(f"Vector features failed for {stock_code}: {e}")
            if db_conn:
                db_conn.rollback()

        return features

    def _find_similar_stocks(
        self, stock_code: str, db_conn, top_k: int
    ) -> List[Dict]:
        """Query pgvector for similar stocks by cosine distance."""
        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT sv2.stock_code,
                       1 - (sv2.embedding <=> sv1.embedding) as similarity
                FROM stock_vectors sv1
                JOIN stock_vectors sv2 ON sv1.vector_type = sv2.vector_type
                WHERE sv1.stock_code = %s
                  AND sv2.stock_code != %s
                  AND sv1.vector_type = 'combined'
                  AND sv1.embedding IS NOT NULL
                  AND sv2.embedding IS NOT NULL
                ORDER BY sv2.embedding <=> sv1.embedding
                LIMIT %s
            """, (stock_code, stock_code, top_k))
            rows = cur.fetchall()
            cur.close()

            similar = []
            for row in rows:
                sim_stock = row[0]
                sim_score = float(row[1]) if row[1] else 0.0

                ret_5d = self._get_return_5d(sim_stock, db_conn)
                similar.append({
                    "stock_code": sim_stock,
                    "similarity": sim_score,
                    "return_5d": ret_5d,
                })
            return similar

        except Exception as e:
            logger.debug(f"Similarity query failed: {e}")
            if db_conn:
                db_conn.rollback()
            return []

    def _get_return_5d(self, stock_code: str, db_conn) -> Optional[float]:
        """Get 5-day return for a stock from market_data."""
        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT close_price
                FROM market_data
                WHERE stock_code = %s
                ORDER BY trade_date DESC
                LIMIT 6
            """, (stock_code,))
            rows = cur.fetchall()
            cur.close()

            if len(rows) >= 6:
                return float(rows[0][0] / rows[5][0] - 1) if rows[5][0] else 0.0
            return 0.0
        except Exception:
            if db_conn:
                db_conn.rollback()
            return None

    def get_vector_features_from_db(
        self, stock_code: str, db_conn=None
    ) -> Dict:
        """Get all vector similarity features."""
        return self.get_similar_stock_features(stock_code, db_conn)
