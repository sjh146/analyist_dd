"""
PostgreSQL Storage for Market Data
Handles bulk inserts and stock master data management.
"""

import psycopg2
import psycopg2.pool
import pandas as pd
import logging
from datetime import datetime
from typing import Dict

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
                minconn=2,
                maxconn=10,
                host=self.config.POSTGRES_HOST,
                port=self.config.POSTGRES_PORT,
                dbname=self.config.POSTGRES_DB,
                user=self.config.POSTGRES_USER,
                password=self.config.POSTGRES_PASSWORD,
            )
            logger.info("PostgreSQL pool initialized")
        except Exception as e:
            logger.error(f"Failed to init pool: {e}")

    def _get_conn(self):
        if not self._pool:
            return None
        try:
            return self._pool.getconn()
        except Exception as e:
            logger.error(f"Failed to get connection: {e}")
            return None

    def _put_conn(self, conn):
        if self._pool and conn:
            self._pool.putconn(conn)

    def upsert_stock(self, stock: Dict):
        """Insert or update stock master data."""
        conn = self._get_conn()
        if not conn:
            return

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO stocks (stock_code, stock_name, market, sector)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (stock_code) DO UPDATE SET
                    stock_name = EXCLUDED.stock_name,
                    sector = COALESCE(NULLIF(EXCLUDED.sector, ''), stocks.sector),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (stock["code"], stock["name"], stock["market"], stock.get("sector", "")),
            )
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to upsert stock {stock['code']}: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def save_market_data(self, stock_code: str, df: pd.DataFrame):
        """Bulk insert market data."""
        conn = self._get_conn()
        if not conn:
            return

        try:
            cur = conn.cursor()

            for _, row in df.iterrows():
                try:
                    cur.execute(
                        """
                        INSERT INTO market_data
                            (stock_code, trade_date, open_price, high_price,
                             low_price, close_price, volume)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (stock_code, trade_date) DO UPDATE SET
                            open_price = EXCLUDED.open_price,
                            high_price = EXCLUDED.high_price,
                            low_price = EXCLUDED.low_price,
                            close_price = EXCLUDED.close_price,
                            volume = EXCLUDED.volume
                        """,
                        (
                            stock_code,
                            row.get("trade_date") or row.get("date"),
                            row.get("open"),
                            row.get("high"),
                            row.get("low"),
                            row.get("close"),
                            int(row.get("volume", 0)),
                        ),
                    )
                except Exception as e:
                    logger.error(f"Failed to insert row for {stock_code}: {e}")
                    continue

            conn.commit()
            cur.close()
            logger.info(f"Saved market data for {stock_code} ({len(df)} rows)")

        except Exception as e:
            logger.error(f"Failed to save market data for {stock_code}: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)
