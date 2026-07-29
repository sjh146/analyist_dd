"""
Stock List Collector
Retrieves stock list from PostgreSQL database.
"""

from typing import List, Dict
import logging
import psycopg2

logger = logging.getLogger(__name__)


class StockListCollector:
    """Provides stock list from database for data collection."""

    def __init__(self):
        self._conn = None

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            from app.config import Config
            config = Config()
            self._conn = psycopg2.connect(
                host=config.POSTGRES_HOST,
                port=config.POSTGRES_PORT,
                dbname=config.POSTGRES_DB,
                user=config.POSTGRES_USER,
                password=config.POSTGRES_PASSWORD,
            )
        return self._conn

    def get_all_stocks(self) -> List[Dict]:
        """Get all stocks from database."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT stock_code, stock_name, market, COALESCE(sector, ''),
                       COALESCE(industry, ''), COALESCE(market_cap, 0)
                FROM stocks
                ORDER BY market, stock_code
            """)
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "code": r[0],
                    "name": r[1],
                    "market": r[2],
                    "sector": r[3],
                    "industry": r[4],
                    "market_cap": r[5],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to get stocks from DB: {e}")
            return []

    def get_stocks_by_market(self, market: str) -> List[Dict]:
        """Get stocks filtered by market type."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT stock_code, stock_name, market, COALESCE(sector, ''),
                       COALESCE(industry, ''), COALESCE(market_cap, 0)
                FROM stocks
                WHERE market = %s
                ORDER BY stock_code
            """, (market,))
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "code": r[0],
                    "name": r[1],
                    "market": r[2],
                    "sector": r[3],
                    "industry": r[4],
                    "market_cap": r[5],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to get stocks for market {market}: {e}")
            return []
