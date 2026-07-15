"""
Drift Detector
Detects performance drift and data drift for ML models.
"""

import json
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PERFORMANCE_METRICS = ["accuracy", "precision", "recall", "f1", "roc_auc"]


class DriftDetector:
    """Detects model performance drift and data drift."""

    def __init__(self, pg_conn=None, reference_window: int = 30):
        self.pg_conn = pg_conn
        self.reference_window = reference_window

    # ------------------------------------------------------------------
    # Performance Drift
    # ------------------------------------------------------------------
    def check_performance_drift(
        self, current_metrics: dict, baseline_metrics: dict
    ) -> dict:
        """Compare current vs baseline metrics and detect performance drift.

        Args:
            current_metrics: Dict of current metric_name -> value.
            baseline_metrics: Dict of baseline metric_name -> value.

        Returns:
            Dict with drift_detected, drifted_metrics, and severity.
        """
        drifted_metrics = {}

        for metric in PERFORMANCE_METRICS:
            current_val = current_metrics.get(metric)
            baseline_val = baseline_metrics.get(metric)

            if current_val is None or baseline_val is None:
                continue
            if baseline_val == 0:
                continue

            drop_pct = ((baseline_val - current_val) / baseline_val) * 100.0

            if drop_pct > 5.0:
                drifted_metrics[metric] = round(drop_pct, 2)

        drift_detected = len(drifted_metrics) > 0

        if drift_detected:
            max_drop = max(drifted_metrics.values())
            if max_drop > 10.0:
                severity = "high"
            elif max_drop > 5.0:
                severity = "medium"
            else:
                severity = "low"
        else:
            severity = "low"

        return {
            "drift_detected": drift_detected,
            "drifted_metrics": drifted_metrics,
            "severity": severity,
        }

    # ------------------------------------------------------------------
    # Data Drift (PSI)
    # ------------------------------------------------------------------
    def check_data_drift(
        self, recent_features: pd.DataFrame, reference_features: pd.DataFrame
    ) -> dict:
        """Detect data drift by computing PSI per feature.

        Args:
            recent_features: DataFrame of recent feature values.
            reference_features: DataFrame of reference/baseline feature values.

        Returns:
            Dict with drift_detected, high_drift_features, and psi_values.
        """
        psi_values = {}
        common_cols = recent_features.columns.intersection(reference_features.columns)

        for col in common_cols:
            expected = reference_features[col].dropna().values
            actual = recent_features[col].dropna().values

            if len(expected) == 0 or len(actual) == 0:
                continue

            psi = self._compute_psi(expected, actual)
            psi_values[col] = round(psi, 4)

        high_drift_features = [
            feat for feat, psi in psi_values.items() if psi > 0.2
        ]
        drift_detected = len(high_drift_features) > 0

        return {
            "drift_detected": drift_detected,
            "high_drift_features": high_drift_features,
            "psi_values": psi_values,
        }

    @staticmethod
    def _compute_psi(
        expected: np.ndarray, actual: np.ndarray, bins: int = 10
    ) -> float:
        """Compute Population Stability Index between two distributions.

        PSI = sum((actual_i - expected_i) * ln(actual_i / expected_i))

        Args:
            expected: Reference distribution.
            actual: Current distribution.
            bins: Number of quantile-based bins.

        Returns:
            PSI value (float).
        """
        # Determine bin edges from expected distribution
        eps = 1e-10
        percentiles = np.linspace(0, 100, bins + 1)
        bin_edges = np.percentile(expected, percentiles)
        # Ensure unique edges
        bin_edges = np.unique(bin_edges)
        if len(bin_edges) < 2:
            return 0.0

        # Bin both distributions
        expected_counts, _ = np.histogram(expected, bins=bin_edges)
        actual_counts, _ = np.histogram(actual, bins=bin_edges)

        # Convert to proportions
        expected_pct = expected_counts / max(expected_counts.sum(), 1)
        actual_pct = actual_counts / max(actual_counts.sum(), 1)

        # Add epsilon to avoid log(0) or division by zero
        expected_pct = np.clip(expected_pct, eps, None)
        actual_pct = np.clip(actual_pct, eps, None)

        psi = np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct))
        return float(psi)

    # ------------------------------------------------------------------
    # Baseline & Storage
    # ------------------------------------------------------------------
    def get_baseline_metrics(self, model_version: str) -> dict:
        """Retrieve baseline metrics for a given model version.

        Attempts to read from ml_predictions table if pg_conn is available,
        otherwise returns a mock baseline.

        Args:
            model_version: Model version string (e.g. 'v1.0').

        Returns:
            Dict of metric_name -> value.
        """
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute(
                    """
                    SELECT metrics FROM ml_predictions
                    WHERE model_version = %s AND metrics IS NOT NULL
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (model_version,),
                )
                row = cur.fetchone()
                cur.close()
                if row and row[0]:
                    return row[0] if isinstance(row[0], dict) else json.loads(row[0])
            except Exception as e:
                logger.warning(f"Failed to read baseline metrics from DB: {e}")

        # Return mock baseline
        logger.info("Using mock baseline metrics (no DB baseline available)")
        return {
            "accuracy": 0.85,
            "precision": 0.82,
            "recall": 0.78,
            "f1": 0.80,
            "roc_auc": 0.87,
        }

    def store_drift_result(
        self, stock_code: str, drift_type: str, drift_data: dict
    ) -> None:
        """Store drift detection result to ml_predictions table.

        Args:
            stock_code: Stock code identifier.
            drift_type: Type of drift ('performance' or 'data').
            drift_data: Drift detection result dict.
        """
        if self.pg_conn is None:
            logger.warning(
                "No pg_conn available — drift result not stored to DB. "
                f"stock_code={stock_code}, drift_type={drift_type}"
            )
            return

        try:
            cur = self.pg_conn.cursor()
            cur.execute(
                """
                INSERT INTO ml_predictions
                    (stock_code, prediction_date, model_version,
                     predicted_direction, predicted_change_pct, confidence, features_used, metrics)
                VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (stock_code, prediction_date, model_version) DO NOTHING
                """,
                (
                    stock_code,
                    f"drift_{drift_type}",
                    "drift_detected" if drift_data.get("drift_detected") else "no_drift",
                    0.0,
                    0.0,
                    json.dumps(drift_data.get("drifted_metrics", {})),
                    json.dumps(drift_data),
                ),
            )
            self.pg_conn.commit()
            cur.close()
            logger.info(
                f"Drift result stored: stock={stock_code}, type={drift_type}, "
                f"drift_detected={drift_data.get('drift_detected')}"
            )
        except Exception as e:
            logger.error(f"Failed to store drift result: {e}")
            if self.pg_conn:
                self.pg_conn.rollback()
