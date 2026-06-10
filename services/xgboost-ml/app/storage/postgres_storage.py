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
        return self._pool.getconn() if self._pool else None

    def _put_conn(self, conn):
        if self._pool and conn:
            self._pool.putconn(conn)

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

    def save_prediction(self, prediction: Dict):
        """Save prediction result."""
        conn = self._get_conn()
        if not conn: return
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
        except Exception as e:
            logger.error(f"Failed to save prediction: {e}")
            conn.rollback()
        finally:
            self._put_conn(conn)

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
