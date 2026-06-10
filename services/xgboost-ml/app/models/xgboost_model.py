"""
XGBoost Model
Stock direction prediction model using XGBoost.
"""

import xgboost as xgb
import numpy as np
import joblib
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class XGBoostModel:
    """XGBoost model for stock direction prediction."""

    def __init__(self):
        self.model = None
        self.feature_names = []
        self.params = {
            "n_estimators": 300,
            "max_depth": 7,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 3,
            "gamma": 0.1,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "objective": "binary:logistic",
            "eval_metric": ["logloss", "auc"],
            "random_state": 42,
            "use_label_encoder": False,
        }
        self.is_trained = False

    def train(self, X_train: np.ndarray, y_train: np.ndarray,
              X_val: Optional[np.ndarray] = None,
              y_val: Optional[np.ndarray] = None) -> Dict:
        """
        Train the XGBoost model.
        
        Args:
            X_train: Training features
            y_train: Training labels (0=down, 1=up)
            X_val: Validation features
            y_val: Validation labels
        
        Returns:
            Training metrics
        """
        dtrain = xgb.DMatrix(X_train, label=y_train)
        evals = [(dtrain, "train")]

        if X_val is not None and y_val is not None:
            dval = xgb.DMatrix(X_val, label=y_val)
            evals.append((dval, "eval"))

        self.model = xgb.train(
            self.params,
            dtrain,
            num_boost_round=self.params["n_estimators"],
            evals=evals,
            early_stopping_rounds=50,
            verbose_eval=False,
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

        dmatrix = xgb.DMatrix(features)
        return self.model.predict(dmatrix)

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
            "confidence": abs(prob - 0.5) * 2,  # 0~1 scale
        }

    def feature_importance(self) -> Dict:
        """Get feature importance scores."""
        if self.model is None:
            return {}
        importance = self.model.get_score(importance_type="gain")
        total = sum(importance.values())
        return {k: v / total for k, v in sorted(
            importance.items(), key=lambda x: x[1], reverse=True
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
