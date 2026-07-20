import psycopg2
import psycopg2.pool
import pandas as pd
import logging

from app.config import Config

logger = logging.getLogger(__name__)


class PostgresStorage:
    def __init__(self):
        self.config = Config()
        self._pool = None
        self._init_pool()
        self._ensure_tables()

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

    def _ensure_tables(self):
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS krx_trading (
                    id SERIAL PRIMARY KEY,
                    trade_date DATE NOT NULL,
                    market VARCHAR(10) NOT NULL,
                    investor_type VARCHAR(50) NOT NULL,
                    trading_value BIGINT DEFAULT 0,
                    net_buy BIGINT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (trade_date, market, investor_type)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS krx_derivatives (
                    id SERIAL PRIMARY KEY,
                    trade_date DATE NOT NULL,
                    index_name VARCHAR(50) NOT NULL,
                    open_price DOUBLE PRECISION,
                    high_price DOUBLE PRECISION,
                    low_price DOUBLE PRECISION,
                    close_price DOUBLE PRECISION,
                    volume BIGINT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (trade_date, index_name)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS krx_short_selling (
                    id SERIAL PRIMARY KEY,
                    trade_date DATE NOT NULL,
                    stock_code VARCHAR(12) NOT NULL,
                    stock_name VARCHAR(100),
                    short_volume BIGINT DEFAULT 0,
                    short_value BIGINT DEFAULT 0,
                    total_volume BIGINT DEFAULT 0,
                    short_ratio DOUBLE PRECISION DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (trade_date, stock_code)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS krx_program_trading (
                    id SERIAL PRIMARY KEY,
                    trade_date DATE NOT NULL,
                    market VARCHAR(10) NOT NULL,
                    total_value BIGINT DEFAULT 0,
                    foreign_buy_value BIGINT DEFAULT 0,
                    foreign_sell_value BIGINT DEFAULT 0,
                    net_buy BIGINT DEFAULT 0,
                    exhaustion_rate DOUBLE PRECISION DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (trade_date, market)
                )
            """)
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to create tables: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def save_trading_data(self, df: pd.DataFrame):
        if df.empty:
            return
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            for _, row in df.iterrows():
                cur.execute("""
                    INSERT INTO krx_trading
                        (trade_date, market, investor_type, trading_value, net_buy)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (trade_date, market, investor_type) DO UPDATE SET
                        trading_value = EXCLUDED.trading_value,
                        net_buy = EXCLUDED.net_buy
                """, (
                    row.get("trade_date"), row.get("market"),
                    row.get("investor_type"), row.get("trading_value", 0),
                    row.get("net_buy", 0),
                ))
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save trading data: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def save_derivatives_data(self, df: pd.DataFrame):
        if df.empty:
            return
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            for _, row in df.iterrows():
                cur.execute("""
                    INSERT INTO krx_derivatives
                        (trade_date, index_name, open_price, high_price,
                         low_price, close_price, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (trade_date, index_name) DO UPDATE SET
                        open_price = EXCLUDED.open_price,
                        high_price = EXCLUDED.high_price,
                        low_price = EXCLUDED.low_price,
                        close_price = EXCLUDED.close_price,
                        volume = EXCLUDED.volume
                """, (
                    row.get("trade_date"), row.get("index_name"),
                    row.get("open"), row.get("high"),
                    row.get("low"), row.get("close"),
                    int(row.get("volume", 0)),
                ))
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save derivatives data: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def save_short_selling_data(self, df: pd.DataFrame):
        if df.empty:
            return
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            for _, row in df.iterrows():
                cur.execute("""
                    INSERT INTO krx_short_selling
                        (trade_date, stock_code, stock_name, short_volume,
                         short_value, total_volume, short_ratio)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (trade_date, stock_code) DO UPDATE SET
                        stock_name = EXCLUDED.stock_name,
                        short_volume = EXCLUDED.short_volume,
                        short_value = EXCLUDED.short_value,
                        total_volume = EXCLUDED.total_volume,
                        short_ratio = EXCLUDED.short_ratio
                """, (
                    row.get("trade_date"), row.get("stock_code"),
                    row.get("stock_name"), row.get("short_volume", 0),
                    row.get("short_value", 0), row.get("total_volume", 0),
                    float(row.get("short_ratio", 0)),
                ))
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save short selling data: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def save_program_trading_data(self, df: pd.DataFrame):
        if df.empty:
            return
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            for _, row in df.iterrows():
                cur.execute("""
                    INSERT INTO krx_program_trading
                        (trade_date, market, total_value, foreign_buy_value,
                         foreign_sell_value, net_buy, exhaustion_rate)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (trade_date, market) DO UPDATE SET
                        total_value = EXCLUDED.total_value,
                        foreign_buy_value = EXCLUDED.foreign_buy_value,
                        foreign_sell_value = EXCLUDED.foreign_sell_value,
                        net_buy = EXCLUDED.net_buy,
                        exhaustion_rate = EXCLUDED.exhaustion_rate
                """, (
                    row.get("trade_date"), row.get("market"),
                    row.get("total_value", 0), row.get("foreign_buy_value", 0),
                    row.get("foreign_sell_value", 0), row.get("net_buy", 0),
                    float(row.get("exhaustion_rate", 0)),
                ))
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error(f"Failed to save program trading data: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)
