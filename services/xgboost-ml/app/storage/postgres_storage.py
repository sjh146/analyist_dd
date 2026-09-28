"""
PostgreSQL Storage for XGBoost ML
Handles training data retrieval and prediction storage.
"""

import psycopg2
import psycopg2.pool
import psycopg2.extras
import pandas as pd
import logging
from typing import Dict, List, Optional

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
        """풀에서 연결을 꺼낸다. 죽은 연결이면 버리고 새로 받는다.

        왜(2026-09-28 실측): Postgres 가 연결을 끊었는데 그 연결이 풀에 반납되면 이후 모든
        저장이 연쇄 실패한다(InterfaceError 'connection already closed'). 닫힌 연결은
        close=True 로 돌려주어 풀에서 제거한다.
        """
        if not self._pool:
            return None
        conn = self._pool.getconn()
        if conn is not None and getattr(conn, "closed", 0):
            try:
                self._pool.putconn(conn, close=True)   # 풀에서 제거
            except Exception:  # noqa: BLE001
                pass
            conn = self._pool.getconn()
        return conn

    def _put_conn(self, conn, broken: bool = False):
        """연결 반납. broken=True 면 닫아서 풀에서 버린다(재사용 금지).

        반납 전에 **항상 트랜잭션을 정리**한다(rollback). psycopg2 는 첫 execute 에서 트랜잭션을
        열기 때문에 읽기만 한 연결도 풀에 그대로 돌아가면 `idle in transaction` 으로 남는다 —
        2026-09-28 실측: 그런 연결 하나가 **21시간** 동안 스냅샷을 붙들고 있었고
        (`PgIdleInTransactionTooLong` 알림), 같은 클래스의 버그가 autovacuum 을 막는다.
        읽기 경로는 rollback 이면 충분하고, 쓰기 경로는 이미 commit 후라 no-op 이다.
        """
        if self._pool and conn:
            if not broken:
                self._safe_rollback(conn)
            try:
                self._pool.putconn(conn, close=bool(broken))
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _safe_rollback(conn) -> None:
        """rollback 자체가 예외를 던지지 않게 감싼다.

        죽은 연결에서 rollback() 은 InterfaceError('connection already closed') 를 던지는데,
        그것이 except 블록 안에서 터지면 원래 예외를 덮고 호출자까지 전파된다(2026-09-28 사고).
        """
        try:
            if conn is not None and not getattr(conn, "closed", 0):
                conn.rollback()
        except Exception:  # noqa: BLE001
            pass

    def get_all_stocks(self) -> List[Dict]:
        conn = self._get_conn()
        if not conn: return []
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT stock_code, stock_name, sector FROM stocks")
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        finally:
            self._put_conn(conn)

    def get_training_data(self, days: int = 365) -> Optional[pd.DataFrame]:
        """Get training data from market_data."""
        conn = self._get_conn()
        if not conn: return None
        try:
            query = f"""
                SELECT stock_code, trade_date, close_price, volume,
                       close_price / LAG(close_price, 5) OVER w - 1 as return_5d,
                       close_price / LAG(close_price, 20) OVER w - 1 as return_20d,
                       STDDEV(close_price / LAG(close_price) OVER w - 1)
                           OVER (ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as volatility_20d,
                       AVG(volume) OVER (ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as volume_avg_20,
                       volume / NULLIF(AVG(volume) OVER (ROWS BETWEEN 19 PRECEDING AND CURRENT ROW), 0) as volume_ratio,
                       LEAD(close_price) OVER w / close_price - 1 as future_return
                FROM market_data
                WHERE trade_date >= CURRENT_DATE - INTERVAL '{days} days'
                WINDOW w AS (PARTITION BY stock_code ORDER BY trade_date)
            """
            df = pd.read_sql(query, conn)
            if not df.empty:
                # Create label: 1 if future_return > 0
                df["label"] = (df["future_return"] > 0).astype(int)
            return df
        except Exception as e:
            logger.error(f"Failed to get training data: {e}")
            return None
        finally:
            self._put_conn(conn)

    def save_prediction(self, prediction: Dict) -> bool:
        """예측 1건을 저장한다. 성공하면 True, 실패하면 False(예외를 던지지 않는다).

        왜 이 모양인가(2026-09-28 실측 사고):
          Postgres 가 연결을 끊자 cur.execute 가 OperationalError('server closed the connection
          unexpectedly') 를 던졌고, except 블록의 conn.rollback() 이 다시 InterfaceError
          ('connection already closed') 를 던져 **그 예외가 run_predictions 로 새어나갔다** —
          그날 예측 저장 루프가 통째로 중단돼 ml_predictions 에 행이 하나도 안 남았다
          (아침 피처/시그널에서 '예측 없음'). 게다가 죽은 연결이 풀에 반납돼 연쇄 실패했다.
        이제: 연결 계열 오류면 연결을 버리고 **1회 재시도**, 그래도 실패하면 False 로 보고한다.
        """
        import time as _time
        from app.metrics_integration import on_db_query

        last_error = None
        for attempt in (1, 2):
            conn = self._get_conn()
            if not conn:
                return False
            broken = False
            _t0 = _time.monotonic()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO ml_predictions
                        (stock_code, prediction_date, model_version,
                         predicted_direction, predicted_change_pct, confidence, features_used)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (stock_code, prediction_date, model_version) DO NOTHING
                    """,
                    (
                        prediction["stock_code"],
                        prediction["prediction_date"],
                        prediction.get("model_version", "v1.0"),
                        prediction.get("predicted_direction", "neutral"),
                        prediction.get("predicted_probability", 0),
                        prediction.get("confidence", 0),
                        str(prediction.get("features_used", [])),
                    ),
                )
                conn.commit()
                cur.close()
                on_db_query(_time.monotonic() - _t0)
                return True
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                # 연결이 죽었다 -> 이 연결은 풀에 반납하지 않고 버린 뒤 새 연결로 재시도
                broken = True
                last_error = e
                logger.warning(
                    "prediction save: connection-level error (attempt %s/2): %s", attempt, e
                )
            except Exception as e:  # 데이터 오류 등 -> 재시도해도 같으므로 즉시 보고
                logger.error(f"Failed to save prediction: {e}")
                last_error = e
            finally:
                if broken:
                    self._safe_rollback(conn)
                    self._put_conn(conn, broken=True)
                else:
                    self._put_conn(conn)
            if not broken:
                return False
        logger.error(
            "prediction save failed after retry (%s): %s",
            prediction.get("stock_code"), last_error,
        )
        return False

    def get_active_model_version(self) -> Optional[str]:
        conn = self._get_conn()
        if not conn: return None
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT model_version FROM ml_predictions ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        finally:
            self._put_conn(conn)

    def save_model_version(self, data: Dict):
        """This would save to a model_version table if it existed."""
        logger.info(f"Model version data: {data}")
