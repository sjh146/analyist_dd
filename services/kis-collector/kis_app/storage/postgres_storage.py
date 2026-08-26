"""KIS 컬렉터 PostgreSQL 저장소.

- ``market_data`` : 기존 테이블 그대로 (ON CONFLICT upsert — 중복 방지).
- ``minute_bars``  : 신규 테이블 (기존 DB에는 _ensure_tables()가 보장).
"""
from __future__ import annotations

import logging

import psycopg2
import psycopg2.pool

logger = logging.getLogger("kis_collector.storage")

MINUTE_BARS_DDL = """
CREATE TABLE IF NOT EXISTS minute_bars (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    trade_date DATE NOT NULL,
    "time" CHAR(6) NOT NULL,
    open_price DECIMAL(20,4),
    high_price DECIMAL(20,4),
    low_price DECIMAL(20,4),
    close_price DECIMAL(20,4),
    volume BIGINT,
    trading_value DECIMAL(30,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (stock_code, trade_date, "time")
)
"""


class PostgresStorage:
    """krx-collector PostgresStorage 패턴을 따른 커넥션 풀 기반 저장소.

    테스트에서는 ``pool`` 을 주입해 실제 DB 없이 upsert SQL을 검증한다.
    """

    def __init__(self, config, pool=None):
        self._config = config
        self._pool = pool
        if self._pool is None:
            self._init_pool()
        self._ensure_tables()

    def _init_pool(self):
        c = self._config
        try:
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1, maxconn=5,
                host=c.POSTGRES_HOST, port=c.POSTGRES_PORT,
                dbname=c.POSTGRES_DB, user=c.POSTGRES_USER,
                password=c.POSTGRES_PASSWORD,
            )
        except Exception as e:
            logger.error("PostgreSQL pool 초기화 실패: %s", e)

    def _get_conn(self):
        if not self._pool:
            return None
        try:
            return self._pool.getconn()
        except Exception as e:
            logger.error("커넥션 획득 실패: %s", e)
            return None

    def _put_conn(self, conn):
        if self._pool and conn:
            try:
                self._pool.putconn(conn)
            except Exception as e:
                logger.error("커넥션 반환 실패: %s", e)

    def _ensure_tables(self):
        """minute_bars 테이블 보장 (기존 DB 대응 — 비파괴)."""
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute(MINUTE_BARS_DDL)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_minute_bars_stock_date "
                "ON minute_bars(stock_code, trade_date)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_minute_bars_date "
                "ON minute_bars(trade_date)")
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error("minute_bars 테이블 생성 실패: %s", e)
            conn.rollback()
        finally:
            self._put_conn(conn)

    # ── 유니버스 ───────────────────────────────────────────────────────
    def get_universe(self):
        """market_data에 존재하는 종목 코드 + market (EXCD 결정용).

        반환: [(stock_code: str, market: str)]
        """
        conn = self._get_conn()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT md.stock_code,
                       COALESCE(s.market, 'KOSPI') AS market
                FROM market_data md
                LEFT JOIN stocks s ON md.stock_code = s.stock_code
                ORDER BY md.stock_code
            """)
            rows = cur.fetchall()
            cur.close()
            return [(str(r[0]), str(r[1])) for r in rows]
        except Exception as e:
            logger.error("유니버스 조회 실패: %s", e)
            return []
        finally:
            self._put_conn(conn)

    # ── 일봉 (market_data) ─────────────────────────────────────────────
    def save_market_data(self, stock_code, rows):
        """기존 market_data upsert (UNIQUE(stock_code, trade_date) 충돌 시 갱신)."""
        if not rows:
            return 0
        conn = self._get_conn()
        if not conn:
            return 0
        saved = 0
        try:
            cur = conn.cursor()
            for r in rows:
                cur.execute("""
                    INSERT INTO market_data
                        (stock_code, trade_date, open_price, high_price,
                         low_price, close_price, volume, trading_value)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (stock_code, trade_date) DO UPDATE SET
                        open_price = EXCLUDED.open_price,
                        high_price = EXCLUDED.high_price,
                        low_price = EXCLUDED.low_price,
                        close_price = EXCLUDED.close_price,
                        volume = EXCLUDED.volume,
                        trading_value = EXCLUDED.trading_value
                """, (
                    str(stock_code),
                    r["trade_date"],
                    r["open_price"], r["high_price"], r["low_price"],
                    r["close_price"], r["volume"], r["trading_value"],
                ))
                saved += 1
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error("market_data 저장 실패 (%s): %s", stock_code, e)
            conn.rollback()
            saved = 0
        finally:
            self._put_conn(conn)
        return saved

    # ── 분봉 (minute_bars) ─────────────────────────────────────────────
    def save_minute_bars(self, stock_code, rows):
        """minute_bars upsert (UNIQUE(stock_code, trade_date, "time") 충돌 시 갱신)."""
        if not rows:
            return 0
        conn = self._get_conn()
        if not conn:
            return 0
        saved = 0
        try:
            cur = conn.cursor()
            for r in rows:
                cur.execute("""
                    INSERT INTO minute_bars
                        (stock_code, trade_date, "time", open_price, high_price,
                         low_price, close_price, volume, trading_value)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (stock_code, trade_date, "time") DO UPDATE SET
                        open_price = EXCLUDED.open_price,
                        high_price = EXCLUDED.high_price,
                        low_price = EXCLUDED.low_price,
                        close_price = EXCLUDED.close_price,
                        volume = EXCLUDED.volume,
                        trading_value = EXCLUDED.trading_value
                """, (
                    str(stock_code),
                    r["trade_date"],
                    r["time"],
                    r["open_price"], r["high_price"], r["low_price"],
                    r["close_price"], r["volume"], r["trading_value"],
                ))
                saved += 1
            conn.commit()
            cur.close()
        except Exception as e:
            logger.error("minute_bars 저장 실패 (%s): %s", stock_code, e)
            conn.rollback()
            saved = 0
        finally:
            self._put_conn(conn)
        return saved


class NullStorage:
    """dry-run 전용 no-op 저장소 — 실제 DB 접근 없이 흐름만 기록."""

    def __init__(self, *_args, **_kwargs):
        self.saved_daily = []
        self.saved_minute = []

    def get_universe(self):
        return [("005930", "KOSPI"), ("000660", "KOSDAQ")]

    def save_market_data(self, stock_code, rows):
        self.saved_daily.append((stock_code, len(rows)))
        logger.info("[dry-run] market_data 저장: %s %d행", stock_code, len(rows))
        return len(rows)

    def save_minute_bars(self, stock_code, rows):
        self.saved_minute.append((stock_code, len(rows)))
        logger.info("[dry-run] minute_bars 저장: %s %d행", stock_code, len(rows))
        return len(rows)
