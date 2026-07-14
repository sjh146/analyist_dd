"""
LightGBM Model
Stock direction prediction model using LightGBM.
"""

import lightgbm as lgb
import numpy as np
import joblib
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class LightGBMModel:
    """LightGBM model for stock direction prediction."""

    def __init__(self, model_dir: str = "models"):
        self.model = None
        self.feature_names = []
        self.params = {
            "n_estimators": 300,
            "max_depth": 7,
            "learning_rate": 0.05,
            "num_leaves": 31,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_samples": 20,
            "objective": "binary",
            "metric": ["binary_logloss", "auc"],
            "boosting_type": "gbdt",
            "random_state": 42,
            "verbosity": -1,
        }
        self.is_trained = False

    def train(self, X_train: np.ndarray, y_train: np.ndarray,
              X_val: Optional[np.ndarray] = None,
              y_val: Optional[np.ndarray] = None,
              feature_names: Optional[list] = None) -> Dict:
        """
        Train the LightGBM model.

        Args:
            X_train: Training features
            y_train: Training labels (0=down, 1=up)
            X_val: Validation features
            y_val: Validation labels
            feature_names: Optional list of feature names

        Returns:
            Training metrics
        """
        dtrain = lgb.Dataset(X_train, label=y_train,
                             feature_name=feature_names)
        evals = [dtrain]
        valid_names = ["train"]

        if X_val is not None and y_val is not None:
            dval = lgb.Dataset(X_val, label=y_val,
                               feature_name=feature_names,
                               reference=dtrain)
            evals.append(dval)
            valid_names.append("eval")

        self.model = lgb.train(
            self.params,
            dtrain,
            num_boost_round=self.params["n_estimators"],
            valid_sets=evals,
            valid_names=valid_names,
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)],
        )

        self.is_trained = True

        # Calculate metrics
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
        """
        Predict probability of upward movement.

        Args:
            features: Feature array or matrix

        Returns:
            Probability predictions
        """
        if not self.is_trained or self.model is None:
            logger.warning("Model not trained yet")
            return np.full(len(features) if len(features.shape) > 1 else 1, 0.5)

        return self.model.predict(features)

    def predict_single(self, features: np.ndarray) -> Dict:
        """
        Predict for a single stock.

        Args:
            features: 1D feature array

        Returns:
            Dict with prediction results
        """
        if len(features.shape) == 1:
            features = features.reshape(1, -1)

        prob = float(self.predict(features)[0])

        return {
            "predicted_probability": prob,
            "predicted_direction": "up" if prob > 0.5 else "down",
            "confidence": abs(prob - 0.5) * 2,
        }

    def feature_importance(self) -> Dict:
        """Get feature importance scores."""
        if self.model is None:
            return {}
        importance = self.model.feature_importance(importance_type="gain")
        feature_names = self.model.feature_name()
        result = dict(zip(feature_names, importance))
        total = sum(result.values())
        return {k: v / total for k, v in sorted(
            result.items(), key=lambda x: x[1], reverse=True
        )}

    def save(self, path: str):
        """Save model to file."""
        if self.model:
            joblib.dump({"model": self.model, "params": self.params}, path)
            logger.info(f"Model saved to {path}")

    def load(self, path: str):
        """Load model from file."""
        data = joblib.load(path)
        self.model = data["model"]
        self.params = data.get("params", self.params)
        self.is_trained = True
        logger.info(f"Model loaded from {path}")

    def get_params(self) -> Dict:
        """Return current model parameters."""
        return dict(self.params)
