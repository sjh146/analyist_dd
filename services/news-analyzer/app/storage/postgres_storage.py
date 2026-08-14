"""
PostgreSQL Storage for News/SNS Analyzer
Handles inserting and updating analysis results.
"""

import psycopg2
import psycopg2.pool
import json
import logging
from datetime import datetime
from typing import Optional, Dict, List

from app.config import Config
from app.models.schemas import Article, AnalysisResult, StockSentiment, StructuredNews
from app.events.clusterer import EventCluster

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

    def save_event_extraction(self, article_id: int, structured: StructuredNews):
        """Insert structured event extraction (JSONB + article FK).

        article_id는 news_analysis 저장 후 해당 행의 id. stock_code는
        이미 stocks 테이블에 존재하는 유효 코드만 전달된다(관계 무단 생성 방지).
        """
        conn = self._get_conn()
        if not conn:
            logger.error("No DB connection available")
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO news_event_extraction
                    (article_id, stock_code, event_type, themes,
                     sentiment_score, importance, novelty, time_range,
                     core_event_text, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    article_id,
                    structured.stock_code,
                    structured.event_type,
                    json.dumps(structured.themes, ensure_ascii=False),
                    structured.sentiment_score,
                    structured.importance,
                    structured.novelty,
                    structured.time_range,
                    structured.core_event_text,
                    json.dumps(
                        {
                            "stock_code": structured.stock_code,
                            "event_type": structured.event_type,
                            "themes": structured.themes,
                            "sentiment_score": structured.sentiment_score,
                            "importance": structured.importance,
                            "novelty": structured.novelty,
                            "time_range": structured.time_range,
                            "core_event_text": structured.core_event_text,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save event extraction: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def save_event_cluster(self, cluster: EventCluster):
        """Upsert an event cluster into news_events (Phase 3).

        embedding vector(384) 컬럼은 Phase 4에서 채워진다. Phase 3에서는 저장하지 않는다.
        """
        conn = self._get_conn()
        if not conn:
            logger.error("No DB connection available")
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO news_events
                    (stock_code, event_type, event_date, time_bucket,
                     cluster_key, article_count, first_article_at,
                     last_article_at, total_importance, max_sentiment_abs,
                     representative_core_event_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cluster_key) DO UPDATE SET
                    stock_code = EXCLUDED.stock_code,
                    event_type = EXCLUDED.event_type,
                    event_date = EXCLUDED.event_date,
                    time_bucket = EXCLUDED.time_bucket,
                    article_count = EXCLUDED.article_count,
                    first_article_at = EXCLUDED.first_article_at,
                    last_article_at = EXCLUDED.last_article_at,
                    total_importance = EXCLUDED.total_importance,
                    max_sentiment_abs = EXCLUDED.max_sentiment_abs,
                    representative_core_event_text = EXCLUDED.representative_core_event_text
                """,
                (
                    cluster.stock_code,
                    cluster.event_type,
                    cluster.event_date,
                    cluster.time_bucket,
                    cluster.cluster_key,
                    cluster.article_count,
                    cluster.first_article_at,
                    cluster.last_article_at,
                    cluster.total_importance,
                    cluster.max_sentiment_abs,
                    cluster.representative_core_event_text,
                ),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save event cluster: {e}")
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

    def get_recent_event_extractions(self, since: datetime) -> List[Dict]:
        """Read recent news_event_extraction rows for clustering (Phase 3)."""
        conn = self._get_conn()
        if not conn:
            return []

        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT stock_code, event_type, created_at, importance,
                       sentiment_score, core_event_text
                FROM news_event_extraction
                WHERE created_at >= %s
                """,
                (since,),
            )
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "stock_code": r[0],
                    "event_type": r[1],
                    "created_at": r[2],
                    "importance": r[3],
                    "sentiment_score": r[4],
                    "core_event_text": r[5],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to read event extractions: {e}")
            return []
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
