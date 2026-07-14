"""
Model Manager
Handles model version tracking, metric storage, and cleanup.
"""

import os
import json
import glob
import logging
from typing import Dict, Optional, List, Union
from datetime import datetime

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages model versions, metrics, and lifecycle."""

    VALID_MODEL_TYPES = {'xgboost', 'lightgbm', 'catboost', 'ensemble'}

    def __init__(self, storage=None, storage_path: str = "models"):
        self.storage = storage
        self.storage_path = storage_path
        self._registry_path = os.path.join(storage_path, "model_registry.json")

    def _ensure_registry(self) -> dict:
        if not os.path.exists(self._registry_path):
            return {}
        try:
            with open(self._registry_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load registry: {e}")
            return {}

    def _save_registry(self, registry: dict):
        os.makedirs(os.path.dirname(self._registry_path), exist_ok=True)
        with open(self._registry_path, "w") as f:
            json.dump(registry, f, indent=2)

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

    def save_model(self, model_type: str, model, metrics: dict = None) -> Optional[int]:
        """Save a model with type tag and version tracking.

        Args:
            model_type: One of 'xgboost', 'lightgbm', 'catboost', 'ensemble'.
            model: Trained model instance with a .save(path) method.
            metrics: Optional dict with accuracy/f1 keys.

        Returns:
            Version number if saved, None on failure.
        """
        try:
            if model_type not in self.VALID_MODEL_TYPES:
                logger.warning(f"Unknown model type: {model_type}")
                return None

            if model is None:
                logger.warning(f"Cannot save None model for {model_type}")
                return None

            if not hasattr(model, 'save'):
                logger.warning(f"Model {model_type} has no save method")
                return None

            registry = self._ensure_registry()
            model_versions = registry.get(model_type, [])
            version = len(model_versions) + 1

            model_dir = os.path.join(self.storage_path, model_type)
            os.makedirs(model_dir, exist_ok=True)
            filename = f"{model_type}_v{version}.joblib"
            filepath = os.path.join(model_dir, filename)

            model.save(filepath)

            entry = {
                "version": version,
                "model_type": model_type,
                "path": os.path.abspath(filepath),
                "created_at": datetime.now().isoformat(),
                "metrics": metrics or {},
            }
            model_versions.append(entry)
            registry[model_type] = model_versions
            self._save_registry(registry)

            logger.info(f"Model {model_type} v{version} saved to {filepath}")
            return version

        except Exception as e:
            logger.error(f"Failed to save model {model_type}: {e}")
            return None

    def load_model(self, model_type: str, version: int = None):
        """Load a saved model by type and optional version.

        Args:
            model_type: Model type to load.
            version: Specific version number; if None loads the latest.

        Returns:
            Loaded model data, or None if not found / error.
        """
        try:
            registry = self._ensure_registry()
            model_versions = registry.get(model_type, [])
            if not model_versions:
                logger.warning(f"No versions found for {model_type}")
                return None

            if version is not None:
                entry = next((v for v in model_versions if v["version"] == version), None)
                if not entry:
                    logger.warning(f"Version {version} not found for {model_type}")
                    return None
            else:
                entry = model_versions[-1]

            filepath = entry["path"]
            if not os.path.exists(filepath):
                logger.warning(f"Model file not found: {filepath}")
                return None

            import joblib
            data = joblib.load(filepath)
            logger.info(f"Loaded {model_type} v{entry['version']} from {filepath}")
            return data

        except Exception as e:
            logger.error(f"Failed to load {model_type}: {e}")
            return None

    def get_all_versions(self, model_type: str = None) -> Union[List, Dict]:
        """Get tracked versions, optionally filtered by model type.

        Args:
            model_type: If specified, return list of versions for that type.
                        If None, return dict of {model_type: [versions]}.

        Returns:
            List or dict of version metadata entries.
        """
        try:
            registry = self._ensure_registry()
            if model_type:
                return registry.get(model_type, [])
            return registry
        except Exception as e:
            logger.error(f"Failed to get versions: {e}")
            return {} if model_type is None else []

    def get_best_model(self, model_type: str = None) -> Optional[str]:
        """Get the path to the best performing model.

        Args:
            model_type: If specified, find best within that type.
                        If None, fall back to file-based best (existing behavior).

        Returns:
            Path string or None.
        """
        try:
            if model_type:
                registry = self._ensure_registry()
                versions = registry.get(model_type, [])
                if not versions:
                    return None

                def _score(v):
                    m = v.get("metrics", {})
                    return m.get("f1", m.get("accuracy", 0)) or 0

                best = max(versions, key=_score)
                return best.get("path")

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
