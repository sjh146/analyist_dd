"""
Model Manager
Handles model version tracking, metric storage, and cleanup.
"""

import os
import json
import glob
import logging
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages model versions, metrics, and lifecycle."""

    def __init__(self, storage):
        self.storage = storage

    def save_model_version(
        self, version: str, metrics: Dict,
        feature_count: int = 0, n_samples: int = 0,
    ):
        """Save model version metadata to PostgreSQL."""
        try:
            conn = getattr(self.storage, "_get_conn", None)
            if not conn:
                logger.warning("Cannot save model version: no DB _get_conn method")
                return
            conn_obj = conn()
            if not conn_obj:
                return

            cur = conn_obj.cursor()
            params = {
                "version": version,
                "accuracy": metrics.get("val_accuracy", metrics.get("accuracy", 0)),
                "precision": metrics.get("precision", 0),
                "recall": metrics.get("recall", 0),
                "f1": metrics.get("f1", 0),
                "feature_count": feature_count,
                "n_samples": n_samples,
                "training_date": datetime.now().isoformat(),
            }

            cur.execute("""
                INSERT INTO strategy_config (strategy_name, strategy_type, parameters, is_active)
                VALUES (%s, 'ml_model', %s, true)
                ON CONFLICT (strategy_name) DO UPDATE SET
                    parameters = EXCLUDED.parameters,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                f"xgboost_{version}",
                json.dumps(params),
            ))
            conn_obj.commit()
            cur.close()
            logger.info(f"Model version {version} saved: f1={params['f1']:.3f}")

        except Exception as e:
            logger.error(f"Failed to save model version: {e}")

    def get_best_model(self) -> Optional[str]:
        """Get the path to the model with highest F1 score."""
        try:
            models_dir = os.environ.get(
                "MODEL_PATH",
                os.path.join(os.path.dirname(__file__), "..", "..", "..",
                             "..", "models", "saved_models"),
            )
            models_dir = os.path.abspath(models_dir)
            model_files = glob.glob(os.path.join(models_dir, "xgboost_*.joblib"))

            if not model_files:
                return None

            return sorted(model_files, key=os.path.getmtime, reverse=True)[0]

        except Exception as e:
            logger.error(f"Failed to find best model: {e}")
            return None

    def cleanup_old_models(self, keep_last: int = 5):
        """Remove old model files, keeping the N most recent."""
        try:
            models_dir = os.environ.get(
                "MODEL_PATH",
                os.path.join(os.path.dirname(__file__), "..", "..", "..",
                             "..", "models", "saved_models"),
            )
            models_dir = os.path.abspath(models_dir)
            model_files = sorted(
                glob.glob(os.path.join(models_dir, "xgboost_*.joblib")),
                key=os.path.getmtime, reverse=True,
            )

            for old_file in model_files[keep_last:]:
                os.remove(old_file)
                logger.info(f"Removed old model: {old_file}")

        except Exception as e:
            logger.debug(f"Model cleanup failed: {e}")

    def get_latest_metrics(self) -> Dict:
        """Get metrics for the latest model version."""
        metrics = {}
        try:
            conn = getattr(self.storage, "_get_conn", None)
            if not conn:
                return metrics
            conn_obj = conn()
            if not conn_obj:
                return metrics

            cur = conn_obj.cursor()
            cur.execute("""
                SELECT parameters
                FROM strategy_config
                WHERE strategy_type = 'ml_model'
                ORDER BY updated_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()
            cur.close()

            if row and row[0]:
                metrics = row[0] if isinstance(row[0], dict) else json.loads(row[0])

        except Exception as e:
            logger.debug(f"Failed to get latest metrics: {e}")

        return metrics
