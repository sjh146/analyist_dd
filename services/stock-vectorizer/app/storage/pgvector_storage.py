"""
pgvector Storage for Stock Vectors
Handles vector storage, retrieval, and similarity search.
"""

import psycopg2
import psycopg2.pool
import psycopg2.extras
import numpy as np
import json
import logging
from typing import List, Dict, Optional

from app.config import Config

logger = logging.getLogger(__name__)


class PgvectorStorage:
    def __init__(self):
        self.config = Config()
        self._pool = None
        self._init_pool()

    def _init_pool(self):
        try:
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=2, maxconn=10,
                host=self.config.POSTGRES_HOST, port=self.config.POSTGRES_PORT,
                dbname=self.config.POSTGRES_DB, user=self.config.POSTGRES_USER,
                password=self.config.POSTGRES_PASSWORD,
            )
        except Exception as e:
            logger.error(f"Failed to init pool: {e}")

    def _get_conn(self):
        return self._pool.getconn() if self._pool else None

    def _put_conn(self, conn):
        if self._pool and conn:
            self._pool.putconn(conn)

    def save_vector(
        self, stock_code: str, vector_type: str,
        embedding: np.ndarray, metadata: Optional[Dict] = None
    ):
        """Save or update a stock vector."""
        conn = self._get_conn()
        if not conn:
            return

        try:
            cur = conn.cursor()
            vector_str = "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"
            metadata_json = json.dumps(metadata) if metadata else "{}"

            cur.execute(
                """
                INSERT INTO stock_vectors (stock_code, vector_type, embedding, metadata)
                VALUES (%s, %s, %s::vector, %s::jsonb)
                ON CONFLICT (stock_code, vector_type) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    metadata = EXCLUDED.metadata,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (stock_code, vector_type, vector_str, metadata_json),
            )
            conn.commit()
            cur.close()
            logger.debug(f"Saved {vector_type} vector for {stock_code}")
        except Exception as e:
            logger.error(f"Failed to save vector for {stock_code}: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def find_similar_stocks(
        self, stock_code: str, vector_type: str = "combined",
        top_k: int = 10, threshold: float = 0.5
    ) -> List[Dict]:
        """
        Find similar stocks using cosine similarity.
        
        Args:
            stock_code: Target stock code
            vector_type: Type of vector to compare
            top_k: Number of results
            threshold: Minimum similarity score
        
        Returns:
            List of similar stocks with similarity scores
        """
        conn = self._get_conn()
        if not conn:
            return []

        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                """
                SELECT
                    sv.stock_code,
                    s.stock_name,
                    s.sector,
                    1 - (sv.embedding <=> (
                        SELECT embedding FROM stock_vectors
                        WHERE stock_code = %s AND vector_type = %s
                    )) as similarity
                FROM stock_vectors sv
                JOIN stocks s ON sv.stock_code = s.stock_code
                WHERE sv.vector_type = %s
                    AND sv.stock_code != %s
                    AND 1 - (sv.embedding <=> (
                        SELECT embedding FROM stock_vectors
                        WHERE stock_code = %s AND vector_type = %s
                    )) > %s
                ORDER BY similarity DESC
                LIMIT %s
                """,
                (stock_code, vector_type, vector_type, stock_code,
                 stock_code, vector_type, threshold, top_k),
            )
            results = cur.fetchall()
            cur.close()
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Failed to find similar stocks for {stock_code}: {e}")
            return []
        finally:
            self._put_conn(conn)
