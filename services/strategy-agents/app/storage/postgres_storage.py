"""
PostgreSQL Storage for Strategy Agents
Fetches market data, stock vectors, and strategy configs.
"""

import psycopg2
import psycopg2.pool
import psycopg2.extras
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta

from app.config import Config

logger = logging.getLogger(__name__)


class PostgresStorage:
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
        conn = self._pool.getconn() if self._pool else None
        if conn:
            conn.autocommit = True
        return conn

    def _put_conn(self, conn):
        if self._pool and conn:
            try:
                conn.rollback()
            except Exception:
                pass
            self._pool.putconn(conn)

    def get_all_stocks(self) -> List[Dict]:
        conn = self._get_conn()
        if not conn: return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT stock_code, stock_name, sector, market FROM stocks")
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        finally:
            self._put_conn(conn)

    def get_strategy_config(self, strategy_name: str) -> Optional[Dict]:
        conn = self._get_conn()
        if not conn: return None
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT parameters FROM strategy_config WHERE strategy_name = %s AND is_active = true",
                (strategy_name,),
            )
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        finally:
            self._put_conn(conn)

    def get_latest_momentum(self, stock_code: str) -> float:
        conn = self._get_conn()
        if not conn: return 0
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT (close_price / LAG(close_price, 20) OVER (ORDER BY trade_date) - 1) as momentum
                FROM market_data
                WHERE stock_code = %s
                ORDER BY trade_date DESC
                LIMIT 21
                """,
                (stock_code,),
            )
            rows = cur.fetchall()
            cur.close()
            return rows[0][0] if rows and rows[0][0] else 0
        except Exception:
            return 0
        finally:
            self._put_conn(conn)

    def find_similar_stocks(self, stock_code: str, vector_type: str = "combined",
                            top_k: int = 10, threshold: float = 0.7) -> List[Dict]:
        conn = self._get_conn()
        if not conn: return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                """
                SELECT sv.stock_code, s.stock_name, s.sector,
                       1 - (sv.embedding <=> (SELECT embedding FROM stock_vectors
                                             WHERE stock_code = %s AND vector_type = %s)) as similarity
                FROM stock_vectors sv
                JOIN stocks s ON sv.stock_code = s.stock_code
                WHERE sv.vector_type = %s AND sv.stock_code != %s
                  AND 1 - (sv.embedding <=> (SELECT embedding FROM stock_vectors
                                             WHERE stock_code = %s AND vector_type = %s)) > %s
                ORDER BY similarity DESC
                LIMIT %s
                """,
                (stock_code, vector_type, vector_type, stock_code,
                 stock_code, vector_type, threshold, top_k),
            )
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"Find similar failed: {e}")
            return []
        finally:
            self._put_conn(conn)

    def get_sectors(self) -> List[Dict]:
        conn = self._get_conn()
        if not conn: return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT DISTINCT sector FROM stocks WHERE sector IS NOT NULL")
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        finally:
            self._put_conn(conn)

    def get_sector_momentum(self, sector: str, days: int = 60) -> Optional[float]:
        conn = self._get_conn()
        if not conn: return None
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT AVG(md.close_price / NULLIF(LAG(md.close_price, %s) OVER (
                    PARTITION BY md.stock_code ORDER BY md.trade_date
                ), 0)) - 1 as avg_return
                FROM market_data md
                JOIN stocks s ON md.stock_code = s.stock_code
                WHERE s.sector = %s
                  AND md.trade_date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY md.trade_date DESC
                LIMIT 1
                """,
                (days, sector, days),
            )
            row = cur.fetchone()
            cur.close()
            return row[0] if row and row[0] else None
        except Exception:
            return None
        finally:
            self._put_conn(conn)

    def get_top_stocks_in_sector(self, sector: str, top_n: int = 5) -> List[Dict]:
        conn = self._get_conn()
        if not conn: return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                """
                SELECT s.stock_code, s.stock_name,
                       (md.close_price / NULLIF(LAG(md.close_price, 20) OVER (
                           PARTITION BY md.stock_code ORDER BY md.trade_date
                       ), 0) - 1) as momentum
                FROM stocks s
                JOIN market_data md ON s.stock_code = md.stock_code
                WHERE s.sector = %s
                  AND md.trade_date = (SELECT MAX(trade_date) FROM market_data)
                ORDER BY momentum DESC
                LIMIT %s
                """,
                (sector, top_n),
            )
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            self._put_conn(conn)

    def get_index_return(self, days: int = 60) -> float:
        conn = self._get_conn()
        if not conn: return 0
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT (close_price / LAG(close_price, %s) OVER (ORDER BY trade_date) - 1)
                FROM market_data
                WHERE stock_code = '005930'
                ORDER BY trade_date DESC
                LIMIT 1
                """,
                (days,),
            )
            row = cur.fetchone()
            cur.close()
            return row[0] if row and row[0] else 0
        except Exception:
            return 0
        finally:
            self._put_conn(conn)

    def get_index_volatility(self, days: int = 60) -> float:
        conn = self._get_conn()
        if not conn: return 0
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT STDDEV(close_price / NULLIF(LAG(close_price) OVER (ORDER BY trade_date), 0) - 1)
                FROM market_data
                WHERE stock_code = '005930'
                  AND trade_date >= CURRENT_DATE - INTERVAL '%s days'
                """,
                (days,),
            )
            row = cur.fetchone()
            cur.close()
            return row[0] if row and row[0] else 0
        except Exception:
            return 0
        finally:
            self._put_conn(conn)

    def get_twin_pairs(self, min_correlation: float = 0.8) -> List[Dict]:
        """Get twin stock pairs. This would typically come from Neo4j."""
        return []  # Implement Neo4j query in production

    def get_price_series(self, stock_code: str, days: int = 60) -> List[float]:
        conn = self._get_conn()
        if not conn: return []
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT close_price FROM market_data
                WHERE stock_code = %s
                ORDER BY trade_date DESC
                LIMIT %s
                """,
                (stock_code, days),
            )
            rows = cur.fetchall()
            cur.close()
            return [float(r[0]) for r in reversed(rows)]
        except Exception:
            return []
        finally:
            self._put_conn(conn)
