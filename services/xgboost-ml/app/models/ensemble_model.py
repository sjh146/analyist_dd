import json
import logging
import os
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
        ]
        self.model_names = ['xgboost', 'lightgbm']
        self._is_trained = False

    def train(self, X_train, y_train, X_val=None, y_val=None, feature_names=None):
        metrics = {}
        for name, model in zip(self.model_names, self.models):
            m = model.train(X_train, y_train, X_val, y_val)
            if m:
                for k, v in m.items():
                    metrics[f"{name}_{k}"] = v
        self._is_trained = True
        return metrics

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

    def save_feature_names(self, feature_names: list, path: str = None):
        if path is None:
            path = "models"
        os.makedirs(path, exist_ok=True)
        fp = os.path.join(path, "feature_names.json")
        with open(fp, "w") as f:
            json.dump(feature_names, f)
        logger.info(f"Saved {len(feature_names)} feature names to {fp}")

    def load_feature_names(self, path: str = None) -> list:
        if path is None:
            path = "models"
        fp = os.path.join(path, "feature_names.json")
        if not os.path.exists(fp):
            logger.warning(f"No feature_names.json found at {fp}")
            return []
        with open(fp, "r") as f:
            names = json.load(f)
        logger.info(f"Loaded {len(names)} feature names from {fp}")
        return names

    def load(self, path: str = None):
        if path is None:
            path = "models"
        loaded = 0
        for name, model in zip(self.model_names, self.models):
            try:
                model_path = f"{path}/{name}_model.pkl"
                model.load(model_path)
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load {name} model: {e}")
        if loaded > 0:
            self._is_trained = True

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
