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

        conn.rollback()

        cur = conn.cursor()
        saved_count = 0

        for _, row in df.iterrows():
            try:
                trade_date = (
                    row.get("trade_date")
                    or row.get("date")
                    or row.get("날짜")
                )
                if trade_date is None:
                    logger.warning(f"Skip row for {stock_code}: null trade_date")
                    continue

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
                        trade_date,
                        row.get("open") or row.get("시가"),
                        row.get("high") or row.get("고가"),
                        row.get("low") or row.get("저가"),
                        row.get("close") or row.get("종가"),
                        int(row.get("volume") or row.get("거래량") or 0),
                    ),
                )
                saved_count += 1
            except Exception as e:
                logger.error(f"Failed to insert row for {stock_code}: {e}")
                conn.rollback()
                cur.close()
                cur = conn.cursor()
                continue

        try:
            conn.commit()
            cur.close()
            logger.info(f"Saved market data for {stock_code} ({saved_count} rows)")
        except Exception as e:
            logger.error(f"Failed to commit market data for {stock_code}: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def update_fundamentals(self, data: Dict):
        """Update fundamental data for a stock."""
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            if data.get("market_cap") is not None:
                cur.execute(
                    "UPDATE stocks SET market_cap = %s, updated_at = CURRENT_TIMESTAMP WHERE stock_code = %s",
                    (int(data["market_cap"]), data["stock_code"])
                )
            cur.execute(
                "SELECT id FROM financial_statements WHERE stock_code = %s ORDER BY report_date DESC LIMIT 1",
                (data["stock_code"],)
            )
            row = cur.fetchone()
            if row:
                updates = []
                params = []
                if data.get("per") is not None:
                    updates.append("per = %s"); params.append(float(data["per"]))
                if data.get("pbr") is not None:
                    updates.append("pbr = %s"); params.append(float(data["pbr"]))
                if data.get("roe") is not None:
                    updates.append("roe = %s"); params.append(float(data["roe"]))
                if updates:
                    params.append(data["stock_code"])
                    cur.execute(f"UPDATE financial_statements SET {', '.join(updates)} WHERE stock_code = %s AND report_date = (SELECT MAX(report_date) FROM financial_statements WHERE stock_code = %s)", params + [data["stock_code"]])
            else:
                cur.execute(
                    "INSERT INTO financial_statements (stock_code, report_date, per, pbr, roe) VALUES (%s, CURRENT_DATE, %s, %s, %s)",
                    (data["stock_code"],
                     float(data["per"]) if data.get("per") else None,
                     float(data["pbr"]) if data.get("pbr") else None,
                     float(data["roe"]) if data.get("roe") else None)
                )
            conn.commit()
            cur.close()
            logger.info(f"Updated fundamentals for {data['stock_code']}")
        except Exception as e:
            logger.error(f"Failed to update fundamentals: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def save_us_market_data(self, df):
        import psycopg2
        conn = psycopg2.connect(host=self.host, port=self.port, dbname=self.dbname, user=self.user, password=self.password)
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS us_market_data(id SERIAL PRIMARY KEY,trade_date DATE NOT NULL,index_name VARCHAR(20) NOT NULL,open_price DECIMAL(12,4),high_price DECIMAL(12,4),low_price DECIMAL(12,4),close_price DECIMAL(12,4),volume BIGINT,created_at TIMESTAMP DEFAULT NOW())""")
        for _, r in df.iterrows():
            cur.execute("INSERT INTO us_market_data(trade_date,index_name,open_price,high_price,low_price,close_price,volume) VALUES(%s,%s,%s,%s,%s,%s,%s)",(r['trade_date'],r['index_name'],r.get('open_price'),r.get('high_price'),r.get('low_price'),r.get('close_price'),r.get('volume')))
        conn.commit(); cur.close(); conn.close()
