"""
PostgreSQL Storage for Strategy Agents
Fetches market data, stock vectors, and strategy configs.
"""

import psycopg2
import psycopg2.pool
import psycopg2.extras
import json
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

    def get_all_stocks(self, limit: Optional[int] = None) -> List[Dict]:
        conn = self._get_conn()
        if not conn: return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if limit:
                cur.execute(
                    "SELECT stock_code, stock_name, sector, market, market_cap FROM stocks ORDER BY market_cap DESC NULLS LAST LIMIT %s",
                    (limit,),
                )
            else:
                cur.execute("SELECT stock_code, stock_name, sector, market, market_cap FROM stocks")
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_all_stocks failed: {e}")
            return []
        finally:
            self._put_conn(conn)

    # ------------------------------------------------------------------
    # Factor strategy read methods (additive, quant-book-strategies plan T1)
    # Point-in-time accessors: only rows with report_date/trade_date <=
    # asof_date are returned so factor computations carry no look-ahead bias.
    # ------------------------------------------------------------------

    def get_financial_statements(self, stock_code: str, asof_date=None) -> List[Dict]:
        """Point-in-time financial statements: report_date <= asof_date, ascending.

        Args:
            stock_code: Stock code (e.g. '005930').
            asof_date: 'YYYY-MM-DD' or date. Only rows disclosed on or before
                this date are returned (future reports excluded). None = all rows.
        """
        conn = self._get_conn()
        if not conn: return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if asof_date is not None:
                cur.execute(
                    "SELECT * FROM financial_statements WHERE stock_code = %s AND report_date <= %s ORDER BY report_date ASC",
                    (stock_code, asof_date),
                )
            else:
                cur.execute(
                    "SELECT * FROM financial_statements WHERE stock_code = %s ORDER BY report_date ASC",
                    (stock_code,),
                )
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_financial_statements failed: {e}")
            return []
        finally:
            self._put_conn(conn)

    def get_latest_financials(self, stock_code: str) -> Optional[Dict]:
        """Latest financial statement row (all-time, no asof cut)."""
        rows = self.get_financial_statements(stock_code)
        return rows[-1] if rows else None

    def get_market_caps(self) -> Dict[str, Optional[float]]:
        """Return {stock_code: market_cap} for every stock (NULL caps kept)."""
        conn = self._get_conn()
        if not conn: return {}
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT stock_code, market_cap FROM stocks")
            rows = cur.fetchall()
            cur.close()
            return {r["stock_code"]: r["market_cap"] for r in rows}
        except Exception as e:
            logger.error(f"get_market_caps failed: {e}")
            return {}
        finally:
            self._put_conn(conn)

    def get_avg_trading_value(self, stock_code: str, days: int = 30) -> Optional[float]:
        """Average trading_value over the last `days` rows (latest first)."""
        return self.get_avg_trading_value_asof(stock_code, days=days, asof_date=None)

    def get_avg_trading_value_asof(self, stock_code: str, days: int = 30, asof_date=None) -> Optional[float]:
        """Average trading_value over the last `days` rows with trade_date <= asof_date."""
        conn = self._get_conn()
        if not conn: return None
        try:
            cur = conn.cursor()
            if asof_date is not None:
                cur.execute(
                    """
                    SELECT AVG(trading_value) FROM (
                        SELECT trading_value FROM market_data
                        WHERE stock_code = %s AND trade_date <= %s
                        ORDER BY trade_date DESC
                        LIMIT %s
                    ) sub
                    """,
                    (stock_code, asof_date, days),
                )
            else:
                cur.execute(
                    """
                    SELECT AVG(trading_value) FROM (
                        SELECT trading_value FROM market_data
                        WHERE stock_code = %s
                        ORDER BY trade_date DESC
                        LIMIT %s
                    ) sub
                    """,
                    (stock_code, days),
                )
            row = cur.fetchone()
            cur.close()
            return float(row[0]) if row and row[0] is not None else None
        except Exception as e:
            logger.error(f"get_avg_trading_value failed: {e}")
            return None
        finally:
            self._put_conn(conn)

    def get_first_trade_date(self, stock_code: str) -> Optional[str]:
        """Earliest trade_date in market_data for a stock (listing-age check)."""
        conn = self._get_conn()
        if not conn: return None
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT MIN(trade_date) FROM market_data WHERE stock_code = %s",
                (stock_code,),
            )
            row = cur.fetchone()
            cur.close()
            return str(row[0]) if row and row[0] else None
        except Exception as e:
            logger.error(f"get_first_trade_date failed: {e}")
            return None
        finally:
            self._put_conn(conn)

    def get_price_series_asof(self, stock_code: str, days: int = 60, asof_date=None) -> List[float]:
        """Ascending close_price series ending at (inclusive) asof_date, up to `days` rows."""
        conn = self._get_conn()
        if not conn: return []
        try:
            cur = conn.cursor()
            if asof_date is not None:
                cur.execute(
                    """
                    SELECT close_price FROM market_data
                    WHERE stock_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC
                    LIMIT %s
                    """,
                    (stock_code, asof_date, days),
                )
            else:
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
        except Exception as e:
            logger.error(f"get_strategy_config failed: {e}")
            return None
        finally:
            self._put_conn(conn)

    def upsert_strategy_config(self, strategy_name: str, strategy_type: str, parameters: Dict, is_active: bool = True) -> bool:
        conn = self._get_conn()
        if not conn: return False
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO strategy_config (strategy_name, strategy_type, parameters, is_active, updated_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (strategy_name) DO UPDATE SET
                    strategy_type = EXCLUDED.strategy_type,
                    parameters = EXCLUDED.parameters,
                    is_active = EXCLUDED.is_active,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (strategy_name, strategy_type, json.dumps(parameters), is_active),
            )
            cur.close()
            return True
        except Exception as e:
            logger.error(f"upsert_strategy_config failed: {e}")
            return False
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
            return [{"name": r["sector"]} for r in rows]
        except Exception as e:
            logger.error(f"get_sectors failed: {e}")
            return []
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
        """Get twin stock pairs by computing correlation from market_data.

        O(n²) 폭주 방지: 전체 기간 대신 최근 TWIN_LOOKBACK_DAYS(기본 60일)만 사용.
        시세 데이터는 96만 행 × 전체 쌍 조인 시 pgsql_tmp 수십 GB 생성 → 기간 제한으로
        쌍 수를 급감시켜 안전하게 동작한다.
        """
        conn = self._get_conn()
        if not conn: return []
        lookback = int(getattr(self, "twin_lookback_days", 0) or 60)
        n_kospi = int(getattr(self, "twin_universe_kospi", 0) or 100)
        n_kosdaq = int(getattr(self, "twin_universe_kosdaq", 0) or 100)
        seed = int(getattr(self, "twin_universe_seed", 42) or 42)
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # 유니버스 (2026-08 수정): 거래대금 상위 N → KOSPI/KOSDAQ 층화 무작위
            # + ETF/ETN 제외 (universe.py의 패턴과 동일, SQL 복제). seed 고정 →
            # setseed()로 재현 가능. 전체 종목 페어는 O(n²) 폭주라 유니버스 제한 유지.
            # setseed는 [-1,1] 범위만 허용 → 정수 시드를 [-1,1)로 정규화.
            norm_seed = ((seed % 2000) / 1000.0) - 1.0
            cur.execute("SELECT setseed(%s)", (norm_seed,))
            cur.execute("""
                WITH recent AS (
                    SELECT stock_code, trade_date, close_price
                    FROM market_data
                    WHERE trade_date >= CURRENT_DATE - %s::int
                ),
                -- ETF/ETN/파생상품 이름 패턴 (services/xgboost-ml/app/training/universe.py 와 동일)
                -- 주의: psycopg2에서 리터럴 퍼센트는 %% 로 이스케이프 필수 (주석에도 퍼센트 문자 금지)
                eligible AS (
                    SELECT s.stock_code, s.market
                    FROM stocks s
                    WHERE s.market IN ('KOSPI', 'KOSDAQ')
                      AND NOT (
                            s.stock_name ILIKE '%%ETN%%' OR s.stock_name ILIKE '%%ETF%%'
                         OR s.stock_name ILIKE '%%레버리지%%' OR s.stock_name ILIKE '%%인버스%%'
                         OR s.stock_name ILIKE '%%리버스%%' OR s.stock_name ILIKE '%%KODEX%%'
                         OR s.stock_name ILIKE '%%TIGER%%' OR s.stock_name ILIKE '%%RISE%%'
                         OR s.stock_name ILIKE '%%HANARO%%' OR s.stock_name ILIKE '%%ARIRANG%%'
                         OR s.stock_name ILIKE '%%KBSTAR%%' OR s.stock_name ILIKE '%%커버드콜%%'
                         OR s.stock_name ILIKE '%%국고채%%' OR s.stock_name ILIKE '%%채권%%'
                         OR s.stock_name ILIKE '%%파생%%' OR s.stock_name ILIKE '%%선물%%'
                         OR s.stock_name ILIKE '%%골드%%' OR s.stock_name ILIKE '%%원유%%'
                         OR s.stock_name ILIKE '%%천연가스%%' OR s.stock_name ILIKE '%%금선물%%'
                         OR s.stock_name ILIKE '%%은선물%%' OR s.stock_name ILIKE '%%리츠%%'
                         OR s.stock_name ILIKE '%%2X%%' OR s.stock_name ILIKE '%%3X%%'
                            )
                      AND EXISTS (
                            SELECT 1 FROM market_data m2
                            WHERE m2.stock_code = s.stock_code
                              AND m2.trade_date >= CURRENT_DATE - 5
                      )
                ),
                -- 층화 무작위: 시장별 rn 부여 후 각각 상위 N개 (setseed로 재현 가능)
                top AS (
                    SELECT stock_code FROM (
                        SELECT stock_code, market,
                               ROW_NUMBER() OVER (PARTITION BY market ORDER BY random()) AS rn
                        FROM eligible
                    ) t
                    WHERE (market = 'KOSPI' AND rn <= %s)
                       OR (market = 'KOSDAQ' AND rn <= %s)
                )
                SELECT a.stock_code as stock_code_a,
                       b.stock_code as stock_code_b,
                       CORR(a.close_price, b.close_price) as correlation
                FROM recent a
                JOIN recent b ON a.trade_date = b.trade_date
                JOIN top ta ON ta.stock_code = a.stock_code
                JOIN top tb ON tb.stock_code = b.stock_code
                WHERE a.stock_code < b.stock_code
                GROUP BY a.stock_code, b.stock_code
                HAVING COUNT(*) >= 20
                   AND CORR(a.close_price, b.close_price) >= %s
                ORDER BY correlation DESC
            """, (lookback, n_kospi, n_kosdaq, min_correlation))
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"get_twin_pairs query failed (expected if no data): {e}")
            return []
        finally:
            self._put_conn(conn)

    def get_positions(self) -> List[Dict]:
        conn = self._get_conn()
        if not conn: return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM positions WHERE quantity > 0")
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_positions failed: {e}")
            return []
        finally:
            self._put_conn(conn)

    def get_latest_price(self, stock_code: str) -> Optional[float]:
        conn = self._get_conn()
        if not conn: return None
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT close_price FROM market_data WHERE stock_code = %s ORDER BY trade_date DESC LIMIT 1",
                (stock_code,),
            )
            row = cur.fetchone()
            cur.close()
            return float(row[0]) if row else None
        except Exception:
            return None
        finally:
            self._put_conn(conn)

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
