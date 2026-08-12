"""
scikit-optimize Hyperparameter Tuner
Tunes XGBoost and LightGBM models using skopt.gp_minimize (Gaussian Process
Bayesian optimization). Runs as an alternative/complement to the existing
Optuna TPE+Hyperband optimizer (optuna_optimizer.py) — it does NOT replace it.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_SKOPT_AVAILABLE = True
try:
    from skopt import gp_minimize
    from skopt.space import Integer, Real
    from skopt.utils import use_named_args
except ImportError:
    _SKOPT_AVAILABLE = False
    gp_minimize = None
    Integer = None
    Real = None
    use_named_args = None


class SkoptTuner:
    """Hyperparameter optimizer using scikit-optimize's gp_minimize.

    Complements (does not replace) ``OptunaOptimizer``. Both are run and their
    best validation AUC compared during champion selection.
    """

    def __init__(
        self,
        model_type: str = "xgboost",
        n_calls: int = 30,
        n_initial_points: int = 10,
        random_state: int = 42,
    ):
        self.model_type = model_type
        self.n_calls = n_calls
        self.n_initial_points = n_initial_points
        self.random_state = random_state
        self.result = None
        self.best_params = None
        self.best_value = None

    def optimize(self, X_train, y_train, X_val, y_val) -> Dict:
        """Run gp_minimize over the model hyperparameter space.

        Returns the best hyperparameters as a dict. ``best_value`` holds the
        best validation AUC (positive, since gp_minimize minimizes -AUC).
        """
        if not _SKOPT_AVAILABLE:
            raise ImportError(
                "scikit-optimize is required for gp_minimize tuning. "
                "Install with: pip install scikit-optimize"
            )

        from sklearn.metrics import roc_auc_score

        dimensions = self._space()
        params_names = [dim.name for dim in dimensions]

        @use_named_args(dimensions=dimensions)
        def objective(**params):
            model = self._create_model(params)
            model.train(X_train, y_train, X_val, y_val)
            probs = model.predict(X_val)
            auc = float(roc_auc_score(y_val, probs))
            # gp_minimize minimizes; negate AUC.
            return -auc

        self.result = gp_minimize(
            func=objective,
            dimensions=dimensions,
            n_calls=self.n_calls,
            n_initial_points=self.n_initial_points,
            random_state=self.random_state,
            acq_func="EI",
        )

        self.best_params = dict(zip(params_names, self.result.x))
        self.best_value = float(-self.result.fun)
        logger.info(
            f"skopt gp_minimize complete: best val AUC={self.best_value:.4f} "
            f"params={self.best_params}"
        )
        return self.best_params

    def _space(self) -> List:
        """Return the skopt search-space dimensions for the model type."""
        if self.model_type == "xgboost":
            return [
                Integer(100, 2000, name="n_estimators"),
                Integer(3, 12, name="max_depth"),
                Real(0.01, 0.3, name="learning_rate", prior="log-uniform"),
                Real(1e-8, 10.0, name="reg_alpha", prior="log-uniform"),
                Real(1e-8, 10.0, name="reg_lambda", prior="log-uniform"),
            ]
        if self.model_type == "lightgbm":
            return [
                Integer(100, 2000, name="n_estimators"),
                Integer(16, 256, name="num_leaves"),
                Real(0.01, 0.3, name="learning_rate", prior="log-uniform"),
                Real(1e-8, 10.0, name="reg_alpha", prior="log-uniform"),
                Real(1e-8, 10.0, name="reg_lambda", prior="log-uniform"),
            ]
        raise ValueError(f"Unknown model_type: {self.model_type}")

    def _create_model(self, params: Dict):
        """Create a model instance configured with the given params."""
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

        raise ValueError(f"Unknown model_type: {self.model_type}")

    def get_trial_history(self) -> List[Dict]:
        """Return per-iteration (x, fun) history from the skopt result."""
        if self.result is None:
            return []
        return [
            {
                "iteration": i,
                "value": float(-fun),
                "params": dict(zip([d.name for d in self.result.space.dimensions], x)),
            }
            for i, (x, fun) in enumerate(zip(self.result.x_iters, self.result.func_vals))
        ]
