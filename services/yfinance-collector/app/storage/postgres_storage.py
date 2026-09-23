"""
PostgreSQL Storage for Market Data
Handles bulk inserts and stock master data management.
"""

import psycopg2
import psycopg2.pool
import pandas as pd
import logging
import os
from datetime import date as ddate, datetime
from typing import Dict

from app.config import Config, kst_now

logger = logging.getLogger(__name__)


def _row_date(value):
    """trade_date 후보(pandas Timestamp/datetime/date/str) → date. 실패/결측 시 None."""
    if value is None:
        return None
    try:
        na = pd.isna(value)          # NaN/NaT (스칼라가 아니면 배열이 올 수 있음)
        if isinstance(na, bool) and na:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, ddate):
        return value
    try:
        ts = pd.Timestamp(value)
        return None if pd.isna(ts) else ts.date()
    except Exception:
        return None


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
        """Bulk insert market data.

        기본은 '빈 자리만 채우기'(ON CONFLICT DO NOTHING) — 이 수집기(pykrx 레거시 경로)는
        보조 소스이고, 공식 수집 경로는 KRX OpenAPI(일별매매정보)/KIS 다. 덮어쓰기를 허용하면
        6시간마다 도는 이 잡이 1년치를 비공식 값으로 되돌려 놓는다(실측 2026-09-23: 공식
        재수집 직후에도 18:00 잡이 과거 1년을 pykrx 값으로 덮어쓸 예정이었다).
        덮어쓰기가 필요하면 YF_MARKET_DATA_OVERWRITE=1 로 되돌린다.
        """
        overwrite = os.getenv("YF_MARKET_DATA_OVERWRITE", "0").strip().lower() in ("1", "true", "yes", "on")
        conn = self._get_conn()
        if not conn:
            return

        conn.rollback()

        cur = conn.cursor()
        saved_count = 0
        kept_count = 0
        skipped_unfinished = 0

        conflict_clause = """
                    ON CONFLICT (stock_code, trade_date) DO UPDATE SET
                        open_price = EXCLUDED.open_price,
                        high_price = EXCLUDED.high_price,
                        low_price = EXCLUDED.low_price,
                        close_price = EXCLUDED.close_price,
                        volume = EXCLUDED.volume
        """ if overwrite else """
                    ON CONFLICT (stock_code, trade_date) DO NOTHING
        """

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

                # 당일(KST) 이후 봉은 미완성(장중 스냅샷)이거나 미래 날짜 → 저장하지 않는다.
                # 확정 봉은 다음 날 공식 경로(KRX OpenAPI/KIS)가 넣는다.
                rdate = _row_date(trade_date)
                if rdate is not None and rdate >= kst_now().date():
                    skipped_unfinished += 1
                    continue

                cur.execute(
                    """
                    INSERT INTO market_data
                        (stock_code, trade_date, open_price, high_price,
                         low_price, close_price, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """ + conflict_clause,
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
                if cur.rowcount:
                    saved_count += 1
                else:
                    kept_count += 1
            except Exception as e:
                logger.error(f"Failed to insert row for {stock_code}: {e}")
                conn.rollback()
                cur.close()
                cur = conn.cursor()
                continue

        try:
            conn.commit()
            cur.close()
            if overwrite:
                logger.info(f"Saved market data for {stock_code} ({saved_count} rows/upsert)")
            else:
                logger.info(
                    f"Saved market data for {stock_code} "
                    f"(신규 {saved_count}행, 기존 {kept_count}행 유지)"
                )
            if skipped_unfinished:
                logger.info(
                    f"Skipped {skipped_unfinished} unfinished/future rows for {stock_code} "
                    f"(trade_date >= 오늘 KST — 확정 봉은 다음 날 KRX/KIS 경로가 적재)"
                )
        except Exception as e:
            logger.error(f"Failed to commit market data for {stock_code}: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

    def save_financial_history(self, stock_code: str, rows: list):
        """Accumulate quarterly financial rows (ON CONFLICT upsert).

        Keeps every report_date row (point-in-time backtests) via schema
        UNIQUE(stock_code, report_date). Additive - does not touch the
        existing per/pbr/roe update path.
        """
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            for row in rows:
                cur.execute(
                    """
                    INSERT INTO financial_statements
                        (stock_code, report_date, revenue, operating_profit,
                         net_income, total_assets, total_equity, gross_profit,
                         operating_cash_flow)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (stock_code, report_date) DO UPDATE SET
                        revenue = COALESCE(EXCLUDED.revenue, financial_statements.revenue),
                        operating_profit = COALESCE(EXCLUDED.operating_profit, financial_statements.operating_profit),
                        net_income = COALESCE(EXCLUDED.net_income, financial_statements.net_income),
                        total_assets = COALESCE(EXCLUDED.total_assets, financial_statements.total_assets),
                        total_equity = COALESCE(EXCLUDED.total_equity, financial_statements.total_equity),
                        gross_profit = COALESCE(EXCLUDED.gross_profit, financial_statements.gross_profit),
                        operating_cash_flow = COALESCE(EXCLUDED.operating_cash_flow, financial_statements.operating_cash_flow)
                    """,
                    (
                        stock_code,
                        row.get("report_date"),
                        row.get("revenue"),
                        row.get("operating_profit"),
                        row.get("net_income"),
                        row.get("total_assets"),
                        row.get("total_equity"),
                        row.get("gross_profit"),
                        row.get("operating_cash_flow"),
                    ),
                )
            conn.commit()
            cur.close()
            logger.info(f"Saved {len(rows)} financial rows for {stock_code}")
        except Exception as e:
            logger.error(f"Failed to save financial history for {stock_code}: {e}")
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
