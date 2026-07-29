"""
Optuna Hyperparameter Optimization
Tunes XGBoost, LightGBM, and CatBoost models using Optuna with TPE + Hyperband.
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_OPTUNA_AVAILABLE = True
try:
    import optuna
    from optuna.samplers import TPESampler
    from optuna.pruners import HyperbandPruner
except ImportError:
    _OPTUNA_AVAILABLE = False
    optuna = None
    TPESampler = None
    HyperbandPruner = None


class OptunaOptimizer:
    """Hyperparameter optimizer using Optuna with TPE sampler and Hyperband pruner."""

    def __init__(
        self,
        model_type: str = "xgboost",
        n_trials: int = 100,
        direction: str = "maximize",
        timeout: int = 1800,
    ):
        self.model_type = model_type
        self.n_trials = n_trials
        self.direction = direction
        self.timeout = timeout
        self.study = None
        self.best_params = None

    def optimize(self, X_train, y_train, X_val, y_val) -> Dict:
        if not _OPTUNA_AVAILABLE:
            raise ImportError(
                "optuna is required for hyperparameter optimization. "
                "Install with: pip install optuna"
            )

        from sklearn.metrics import roc_auc_score

        def objective(trial):
            params = self._suggest_params(trial)
            model = self._create_model(params)
            model.train(X_train, y_train, X_val, y_val)
            probs = model.predict(X_val)
            return float(roc_auc_score(y_val, probs))

        self.study = optuna.create_study(
            direction=self.direction,
            sampler=TPESampler(seed=42),
            pruner=HyperbandPruner(),
        )
        self.study.optimize(
            objective,
            n_trials=self.n_trials,
            timeout=self.timeout,
        )
        self.best_params = self.study.best_params
        logger.info(
            f"Optuna optimization complete: best {self.direction} "
            f"value={self.study.best_value:.4f}"
        )
        return self.best_params

    def _suggest_params(self, trial) -> Dict:
        if self.model_type == "xgboost":
            return {
                "n_estimators": trial.suggest_int("n_estimators", 100, 2000, step=100),
                "max_depth": trial.suggest_int("max_depth", 3, 12),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
                "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
            }
        if self.model_type == "lightgbm":
            return {
                "n_estimators": trial.suggest_int("n_estimators", 100, 2000, step=100),
                "num_leaves": trial.suggest_int("num_leaves", 16, 256),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
                "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
            }
        if self.model_type == "catboost":
            return {
                "iterations": trial.suggest_int("iterations", 100, 2000, step=100),
                "depth": trial.suggest_int("depth", 4, 10),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1, 10, log=True),
                "border_count": trial.suggest_int("border_count", 32, 255),
            }
        raise ValueError(f"Unknown model_type: {self.model_type}")

    def _create_model(self, params: Dict):
        if self.model_type == "xgboost":
            from app.models.xgboost_model import XGBoostModel

            model = XGBoostModel()
            model.params.update(
                {k: v for k, v in params.items() if k not in ("n_estimators",)}
            )
            model.n_estimators = params.get("n_estimators", model.n_estimators)
            return model

        if self.model_type == "lightgbm":
            from app.models.lightgbm_model import LightGBMModel

            model = LightGBMModel()
            model.params.update(params)
            model.params["n_estimators"] = params.get("n_estimators", 800)
            return model

        if self.model_type == "catboost":
            from app.models.catboost_model import CatBoostModel

            model = CatBoostModel()
            model.params.update(params)
            return model

        raise ValueError(f"Unknown model_type: {self.model_type}")

    def get_trial_history(self):
        if self.study is None:
            return []
        return [
            {
                "number": t.number,
                "value": t.value,
                "params": t.params,
                "state": str(t.state),
            }
            for t in self.study.trials
            if t.value is not None
        ]
