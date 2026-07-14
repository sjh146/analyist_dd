"""
Feature Store
Persists and retrieves computed features from PostgreSQL.
"""

import logging
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class FeatureStore:
    """Persist and load feature values to/from feature_store.feature_values."""

    def __init__(self, pg_conn=None):
        """pg_conn: psycopg2 connection or None for graceful degradation."""
        self.pg_conn = pg_conn

    def save_features(self, stock_code: str, date: str, features: dict) -> bool:
        """Upsert features to feature_store.feature_values."""
        if self.pg_conn is None:
            logger.warning("No pg_conn: cannot save features")
            return False
        try:
            cursor = self.pg_conn.cursor()
            rows = []
            for name, value in features.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    rows.append((stock_code, date, name, float(value)))
            if not rows:
                return False
            cursor.executemany(
                """
                INSERT INTO feature_store.feature_values
                    (stock_code, date, feature_name, feature_value)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (stock_code, date, feature_name)
                DO UPDATE SET feature_value = EXCLUDED.feature_value
                """,
                rows,
            )
            self.pg_conn.commit()
            cursor.close()
            return True
        except Exception as e:
            logger.error(f"Failed to save features for {stock_code} {date}: {e}")
            return False

    def load_features(self, stock_code: str, date: str) -> dict:
        """Load features for a stock on a date. Returns {} on failure."""
        if self.pg_conn is None:
            logger.warning("No pg_conn: cannot load features")
            return {}
        try:
            cursor = self.pg_conn.cursor()
            cursor.execute(
                """
                SELECT feature_name, feature_value
                FROM feature_store.feature_values
                WHERE stock_code = %s AND date = %s
                """,
                (stock_code, date),
            )
            result = {row[0]: row[1] for row in cursor.fetchall()}
            cursor.close()
            return result
        except Exception as e:
            logger.error(f"Failed to load features for {stock_code} {date}: {e}")
            return {}

    def load_batch(
        self, stock_codes: list, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Load features for multiple stocks. Returns empty DataFrame on failure."""
        if self.pg_conn is None:
            logger.warning("No pg_conn: cannot load batch features")
            return pd.DataFrame()
        try:
            cursor = self.pg_conn.cursor()
            cursor.execute(
                """
                SELECT stock_code, date, feature_name, feature_value
                FROM feature_store.feature_values
                WHERE stock_code = ANY(%s) AND date BETWEEN %s AND %s
                """,
                (stock_codes, start_date, end_date),
            )
            rows = cursor.fetchall()
            cursor.close()
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(
                rows, columns=["stock_code", "date", "feature_name", "feature_value"]
            )
            pivot = df.pivot_table(
                index=["stock_code", "date"],
                columns="feature_name",
                values="feature_value",
            ).reset_index()
            pivot.columns.name = None
            return pivot
        except Exception as e:
            logger.error(f"Failed to load batch features: {e}")
            return pd.DataFrame()

    def get_feature_names(self) -> list:
        """Return all registered feature names from the database."""
        if self.pg_conn is None:
            logger.warning("No pg_conn: cannot get feature names")
            return []
        try:
            cursor = self.pg_conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT feature_name
                FROM feature_store.feature_values
                ORDER BY feature_name
                """
            )
            names = [row[0] for row in cursor.fetchall()]
            cursor.close()
            return names
        except Exception as e:
            logger.error(f"Failed to get feature names: {e}")
            return []
