"""
PostgreSQL Storage for Stock Vectorizer
Handles stock data retrieval for vectorization.
"""

import psycopg2
import psycopg2.pool
import pandas as pd
import logging
from typing import List, Dict, Optional

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
        return self._pool.getconn() if self._pool else None

    def _put_conn(self, conn):
        if self._pool and conn:
            self._pool.putconn(conn)

    def get_all_stocks(self) -> List[Dict]:
        """Get all stocks from database."""
        conn = self._get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute("SELECT stock_code, stock_name, market, sector, industry, market_cap FROM stocks")
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "stock_code": r[0], "stock_name": r[1], "market": r[2],
                    "sector": r[3] or "", "industry": r[4] or "", "market_cap": r[5] or 0,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to get stocks: {e}")
            return []
        finally:
            self._put_conn(conn)

    def get_latest_market_data(self, stock_code: str, days: int = 60) -> pd.DataFrame:
        """Get latest market data for a stock."""
        conn = self._get_conn()
        if not conn:
            return pd.DataFrame()
        try:
            return pd.read_sql(
                """
                SELECT trade_date, open_price, high_price, low_price, close_price, volume
                FROM market_data
                WHERE stock_code = %s
                ORDER BY trade_date DESC
                LIMIT %s
                """,
                conn, params=(stock_code, days),
            )
        except Exception as e:
            logger.debug(f"No market data for {stock_code}: {e}")
            return pd.DataFrame()
        finally:
            self._put_conn(conn)

    def get_latest_sentiment(self, stock_code: str, days: int = 30) -> list:
        """Get latest sentiment data."""
        conn = self._get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT analysis_date, avg_sentiment, sentiment_count
                FROM stock_sentiment
                WHERE stock_code = %s
                ORDER BY analysis_date DESC
                LIMIT %s
                """,
                (stock_code, days),
            )
            rows = cur.fetchall()
            cur.close()
            return [{"date": r[0], "avg_sentiment": r[1], "count": r[2]} for r in rows]
        except Exception as e:
            logger.debug(f"No sentiment for {stock_code}: {e}")
            return []
        finally:
            self._put_conn(conn)

    def get_stock_sector(self, stock_code: str) -> Optional[Dict]:
        """Get stock sector info."""
        conn = self._get_conn()
        if not conn:
            return None
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT sector, industry FROM stocks WHERE stock_code = %s",
                (stock_code,),
            )
            row = cur.fetchone()
            cur.close()
            if row:
                return {"sector": row[0], "industry": row[1]}
            return None
        finally:
            self._put_conn(conn)

    def get_stock_name(self, stock_code: str) -> str:
        """Get stock name by code."""
        conn = self._get_conn()
        if not conn:
            return stock_code
        try:
            cur = conn.cursor()
            cur.execute("SELECT stock_name FROM stocks WHERE stock_code = %s", (stock_code,))
            row = cur.fetchone()
            cur.close()
            return row[0] if row else stock_code
        finally:
            self._put_conn(conn)
