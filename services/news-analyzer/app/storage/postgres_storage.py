"""
PostgreSQL Storage for News/SNS Analyzer
Handles inserting and updating analysis results.
"""

import psycopg2
import psycopg2.pool
import logging
from datetime import datetime
from typing import Optional, Dict

from app.config import Config
from app.models.schemas import Article, AnalysisResult, StockSentiment

logger = logging.getLogger(__name__)


class PostgresStorage:
    def __init__(self):
        self.config = Config()
        self._pool = None
        self._init_pool()

    def _init_pool(self):
        """Initialize connection pool."""
        try:
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                host=self.config.POSTGRES_HOST,
                port=self.config.POSTGRES_PORT,
                dbname=self.config.POSTGRES_DB,
                user=self.config.POSTGRES_USER,
                password=self.config.POSTGRES_PASSWORD,
            )
            logger.info("PostgreSQL connection pool initialized")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL pool: {e}")

    def _get_conn(self):
        """Get connection from pool."""
        if not self._pool:
            return None
        try:
            return self._pool.getconn()
        except Exception as e:
            logger.error(f"Failed to get connection from pool: {e}")
            return None

    def _put_conn(self, conn):
        """Return connection to pool."""
        if self._pool and conn:
            self._pool.putconn(conn)

    def save_news_analysis(self, article: Article, result: AnalysisResult):
        """Insert news analysis into PostgreSQL."""
        conn = self._get_conn()
        if not conn:
            logger.error("No DB connection available")
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO news_analysis
                    (source, title, content, url, published_at,
                     authenticity_score, authenticity_label,
                     sentiment_score, sentiment_label, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                """,
                (
                    article.source,
                    article.title,
                    article.content[:5000] if article.content else None,
                    article.url,
                    article.published_at,
                    result.authenticity_score,
                    result.authenticity_label,
                    result.sentiment_score,
                    result.sentiment_label,
                    result.confidence,
                ),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save news analysis: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def _ensure_validated_at_column(self):
        """Ensure validated_at column exists on stock_sentiment."""
        conn = self._get_conn()
        if not conn:
            return False
        try:
            cur = conn.cursor()
            cur.execute(
                "ALTER TABLE stock_sentiment ADD COLUMN IF NOT EXISTS validated_at TIMESTAMP"
            )
            conn.commit()
            cur.close()
            return True
        except Exception as e:
            logger.warning(
                f"Cannot add validated_at column: {e}. "
                "Run: ALTER TABLE stock_sentiment ADD COLUMN validated_at TIMESTAMP;"
            )
            return False
        finally:
            self._put_conn(conn)

    def save_stock_sentiment(
        self,
        stock_code: str,
        date: datetime.date,
        sentiment_score: float,
        is_news: bool = True,
        validated_at: Optional[datetime] = None,
    ):
        """Upsert stock sentiment data."""
        conn = self._get_conn()
        if not conn:
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO stock_sentiment
                    (stock_code, analysis_date, avg_sentiment,
                     sentiment_count, news_count,
                     positive_count, negative_count, neutral_count,
                     validated_at)
                VALUES (%s, %s, %s, 1, %s,
                        CASE WHEN %s > 0.2 THEN 1 ELSE 0 END,
                        CASE WHEN %s < -0.2 THEN 1 ELSE 0 END,
                        CASE WHEN %s >= -0.2 AND %s <= 0.2 THEN 1 ELSE 0 END,
                        %s)
                ON CONFLICT (stock_code, analysis_date) DO UPDATE SET
                    avg_sentiment = (stock_sentiment.avg_sentiment * stock_sentiment.sentiment_count + %s)
                                    / (stock_sentiment.sentiment_count + 1),
                    sentiment_count = stock_sentiment.sentiment_count + 1,
                    news_count = stock_sentiment.news_count + CASE WHEN %s THEN 1 ELSE 0 END,
                    positive_count = stock_sentiment.positive_count + CASE WHEN %s > 0.2 THEN 1 ELSE 0 END,
                    negative_count = stock_sentiment.negative_count + CASE WHEN %s < -0.2 THEN 1 ELSE 0 END,
                    neutral_count = stock_sentiment.neutral_count + CASE WHEN %s >= -0.2 AND %s <= 0.2 THEN 1 ELSE 0 END
                """,
                (
                    stock_code,
                    date,
                    sentiment_score,
                    1 if is_news else 0,
                    sentiment_score,
                    sentiment_score,
                    sentiment_score,
                    sentiment_score,
                    validated_at,
                    sentiment_score,
                    is_news,
                    sentiment_score,
                    sentiment_score,
                    sentiment_score,
                    sentiment_score,
                ),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save stock sentiment: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def get_analysis_by_url(self, url: str) -> Optional[Dict]:
        """Check if a URL has already been analyzed."""
        conn = self._get_conn()
        if not conn:
            return None

        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, analyzed_at FROM news_analysis WHERE url = %s",
                (url,),
            )
            row = cur.fetchone()
            cur.close()
            if row:
                return {"id": row[0], "analyzed_at": row[1]}
            return None
        except Exception as e:
            logger.error(f"Failed to check analysis: {e}")
            return None
        finally:
            self._put_conn(conn)

    def get_stock_by_name(self, name: str) -> Optional[Dict]:
        """Search stock by name."""
        conn = self._get_conn()
        if not conn:
            return None

        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT stock_code, stock_name, sector FROM stocks WHERE stock_name LIKE %s",
                (f"%{name}%",),
            )
            row = cur.fetchone()
            cur.close()
            if row:
                return {
                    "stock_code": row[0],
                    "stock_name": row[1],
                    "sector": row[2],
                }
            return None
        except Exception as e:
            logger.error(f"Failed to search stock: {e}")
            return None
        finally:
            self._put_conn(conn)

    def close(self):
        """Close all connections."""
        if self._pool:
            self._pool.closeall()
            logger.info("PostgreSQL connection pool closed")
