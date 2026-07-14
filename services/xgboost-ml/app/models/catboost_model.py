"""
CatBoost Model
Stock direction prediction model using CatBoost.
"""

import numpy as np
import joblib
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

try:
    from catboost import CatBoostClassifier, Pool
    _CATBOOST_AVAILABLE = True
except ImportError:
    CatBoostClassifier = None
    Pool = None
    _CATBOOST_AVAILABLE = False
    logger.warning("catboost not installed. Install with: pip install catboost")


class CatBoostModel:
    """CatBoost model for stock direction prediction."""

    def __init__(self):
        self.model = None
        self.feature_names = []
        self.params = {
            "iterations": 300,
            "depth": 7,
            "learning_rate": 0.05,
            "l2_leaf_reg": 3,
            "random_seed": 42,
            "verbose": False,
        }
        self.is_trained = False

    def train(self, X_train: np.ndarray, y_train: np.ndarray,
              X_val: Optional[np.ndarray] = None,
              y_val: Optional[np.ndarray] = None,
              feature_names: Optional[list] = None) -> Dict:
        if not _CATBOOST_AVAILABLE:
            raise ImportError("catboost is required. Install with: pip install catboost")

        train_pool = Pool(X_train, label=y_train, feature_names=feature_names)
        eval_pool = None
        if X_val is not None and y_val is not None:
            eval_pool = Pool(X_val, label=y_val, feature_names=feature_names)

        self.model = CatBoostClassifier(**self.params)
        self.model.fit(
            train_pool,
            eval_set=eval_pool,
            early_stopping_rounds=50,
            verbose=False,
        )

        self.is_trained = True

        train_preds = (self.predict(X_train) > 0.5).astype(int)
        train_acc = np.mean(train_preds == y_train)

        metrics = {"train_accuracy": float(train_acc)}

        if X_val is not None and y_val is not None:
            val_preds = (self.predict(X_val) > 0.5).astype(int)
            val_acc = np.mean(val_preds == y_val)
            metrics["val_accuracy"] = float(val_acc)

        logger.info(f"Training metrics: {metrics}")
        return metrics

    def predict(self, features: np.ndarray) -> np.ndarray:
        if not self.is_trained or self.model is None:
            logger.warning("Model not trained yet")
            return np.full(len(features) if len(features.shape) > 1 else 1, 0.5)
        return self.model.predict_proba(features)[:, 1]

    def predict_single(self, features: np.ndarray) -> Dict:
        if len(features.shape) == 1:
            features = features.reshape(1, -1)
        prob = float(self.predict(features)[0])
        return {
            "predicted_probability": prob,
            "predicted_direction": "up" if prob > 0.5 else "down",
            "confidence": abs(prob - 0.5) * 2,
        }

    def feature_importance(self) -> Dict:
        if self.model is None:
            return {}
        importance = self.model.get_feature_importance(type="PredictionValuesChange")
        total = importance.sum()
        if total == 0:
            return {}
        return {self.feature_names[i]: float(importance[i]) / total for i in range(len(importance))}

    def save(self, path: str):
        if self.model:
            joblib.dump({"model": self.model, "params": self.params}, path)
            logger.info(f"Model saved to {path}")

    def load(self, path: str):
        data = joblib.load(path)
        self.model = data["model"]
        self.params = data.get("params", self.params)
        self.is_trained = True
        logger.info(f"Model loaded from {path}")

    def get_params(self) -> Dict:
        return self.params.copy()
