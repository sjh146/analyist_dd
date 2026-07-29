"""
Scikit-learn Pipeline
Creates sklearn Pipelines with StandardScaler, PCA, and an estimator.
"""

import numpy as np
import joblib
import logging
from typing import Dict, Optional

from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA

logger = logging.getLogger(__name__)


class SklearnPipeline:
    """Pipeline with StandardScaler → PCA → estimator, matching XGBoostModel interface."""

    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline
        self.feature_names = []
        self.trained = False

    @classmethod
    def create_pipeline(cls, estimator):
        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("pca", PCA(n_components=0.95)),
            ("estimator", estimator),
        ])
        return cls(pipeline)

    def train(self, X_train, y_train, X_val=None, y_val=None):
        self.pipeline.fit(X_train, y_train)
        self.trained = True

        train_preds = (self.predict(X_train) > 0.5).astype(int)
        train_acc = float(np.mean(train_preds == y_train))
        return {"train_accuracy": train_acc}

    def predict(self, X):
        if hasattr(self.pipeline.named_steps["estimator"], "predict_proba"):
            return self.pipeline.predict_proba(X)[:, 1]
        return self.pipeline.predict(X)

    def predict_single(self, features):
        probs = self.predict(np.array([features]))
        return float(probs[0])

    def save(self, path: str):
        joblib.dump({"pipeline": self.pipeline, "feature_names": self.feature_names}, path)
        logger.info(f"Sklearn pipeline saved to {path}")

    def load(self, path: str):
        data = joblib.load(path)
        self.pipeline = data["pipeline"]
        self.feature_names = data.get("feature_names", [])
        self.trained = True
        logger.info(f"Sklearn pipeline loaded from {path}")

    def feature_importance(self):
        estimator = self.pipeline.named_steps["estimator"]
        if hasattr(estimator, "feature_importances_"):
            return estimator.feature_importances_
        if hasattr(estimator, "coef_"):
            return estimator.coef_[0]
        return None

    def get_pca_explained_variance(self):
        return self.pipeline.named_steps["pca"].explained_variance_ratio_

    def get_pca_n_components(self):
        return self.pipeline.named_steps["pca"].n_components_
