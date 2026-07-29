"""
Scikit-learn Baseline Models
Wraps sklearn estimators with a standard interface matching XGBoostModel.
"""

import numpy as np
import joblib
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SklearnModelWrapper:
    """Wrapper around sklearn estimators with standard train/predict/save interface."""

    def __init__(self, estimator):
        self.estimator = estimator
        self.trained = False

    def train(self, X_train, y_train, X_val=None, y_val=None):
        self.estimator.fit(X_train, y_train)
        self.trained = True
        return {"train_accuracy": float(self.estimator.score(X_train, y_train))}

    def predict(self, X):
        if hasattr(self.estimator, "predict_proba"):
            return self.estimator.predict_proba(X)[:, 1]
        return self.estimator.predict(X)

    def predict_single(self, features):
        probs = self.predict(np.array([features]))
        return float(probs[0])

    def save(self, path: str):
        joblib.dump({"model": self.estimator}, path)
        logger.info(f"Sklearn model saved to {path}")

    def load(self, path: str):
        data = joblib.load(path)
        model = data.get("model", data)
        self.estimator = model
        self.trained = True
        logger.info(f"Sklearn model loaded from {path}")

    def feature_importance(self):
        if hasattr(self.estimator, "feature_importances_"):
            return self.estimator.feature_importances_
        if hasattr(self.estimator, "coef_"):
            return self.estimator.coef_[0]
        return None


def create_random_forest(n_estimators=200, max_depth=10, class_weight="balanced"):
    """Create a Random Forest classifier wrapped in SklearnModelWrapper."""
    from sklearn.ensemble import RandomForestClassifier

    estimator = RandomForestClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        class_weight=class_weight,
        random_state=42,
        n_jobs=-1,
    )
    return SklearnModelWrapper(estimator)


def create_logistic_regression(C=1.0, penalty="l2", class_weight="balanced"):
    """Create a Logistic Regression classifier wrapped in SklearnModelWrapper."""
    from sklearn.linear_model import LogisticRegression

    estimator = LogisticRegression(
        C=C,
        penalty=penalty,
        class_weight=class_weight,
        random_state=42,
        max_iter=1000,
        solver="lbfgs",
    )
    return SklearnModelWrapper(estimator)


def create_svm(kernel="rbf", C=1.0, gamma="scale", class_weight="balanced"):
    """Create an SVM classifier wrapped in SklearnModelWrapper."""
    from sklearn.svm import SVC

    estimator = SVC(
        kernel=kernel,
        C=C,
        gamma=gamma,
        class_weight=class_weight,
        probability=True,
        random_state=42,
    )
    return SklearnModelWrapper(estimator)
