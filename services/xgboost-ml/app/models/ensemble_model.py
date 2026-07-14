import logging
import numpy as np
from typing import Dict, List, Optional

from .xgboost_model import XGBoostModel
from .lightgbm_model import LightGBMModel
from .catboost_model import CatBoostModel

logger = logging.getLogger(__name__)


class EnsembleModel:
    """
    Soft Voting Ensemble combining XGBoost, LightGBM, and CatBoost.
    Predicts by averaging probability outputs from all models.
    """

    def __init__(self, model_dir: str = "models"):
        self.models = [
            XGBoostModel(),
            LightGBMModel(model_dir),
            CatBoostModel(),
        ]
        self.model_names = ['xgboost', 'lightgbm', 'catboost']
        self._is_trained = False

    def train(self, X_train, y_train, X_val=None, y_val=None, feature_names=None):
        for name, model in zip(self.model_names, self.models):
            model.train(X_train, y_train, X_val, y_val, feature_names)
        self._is_trained = True

    def predict(self, X) -> np.ndarray:
        probs = np.mean([m.predict(X) for m in self.models], axis=0)
        return probs

    def predict_single(self, features: np.ndarray) -> dict:
        results = {}
        model_probs = []
        for name, model in zip(self.model_names, self.models):
            try:
                result = model.predict_single(features)
                results[name] = result
                model_probs.append(result['predicted_probability'])
            except Exception as e:
                logger.warning(f"{name} prediction failed: {e}")
                continue

        if not model_probs:
            raise ValueError("All models failed to predict")

        avg_prob = np.mean(model_probs)
        direction = "up" if avg_prob >= 0.5 else "down"
        confidence = max(avg_prob, 1 - avg_prob)

        return {
            "ensemble": {
                "direction": direction,
                "confidence": float(confidence),
                "probability": float(avg_prob),
            },
            "models": results,
            "model_count": len(model_probs),
        }

    def save(self, path: str = None) -> list:
        if path is None:
            path = "models"
        paths = []
        for name, model in zip(self.model_names, self.models):
            model_path = f"{path}/{name}_model.pkl"
            model.save(model_path)
            paths.append(model_path)
        return paths

    def load(self, path: str = None):
        if path is None:
            path = "models"
        for name, model in zip(self.model_names, self.models):
            try:
                model_path = f"{path}/{name}_model.pkl"
                model.load(model_path)
            except Exception as e:
                logger.warning(f"Failed to load {name} model: {e}")

    def feature_importance(self) -> dict:
        all_importances = {}
        counts = {}
        for name, model in zip(self.model_names, self.models):
            try:
                imp = model.feature_importance()
                for feat, score in imp.items():
                    all_importances[feat] = all_importances.get(feat, 0) + score
                    counts[feat] = counts.get(feat, 0) + 1
            except Exception:
                continue

        return {feat: score / counts[feat] for feat, score in all_importances.items()}

    def get_params(self) -> dict:
        params = {}
        for name, model in zip(self.model_names, self.models):
            try:
                if hasattr(model, 'get_params'):
                    params[name] = model.get_params()
                else:
                    params[name] = {}
            except Exception as e:
                logger.warning(f"Failed to get params for {name}: {e}")
                params[name] = {}
        return params
