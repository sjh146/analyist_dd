"""
Tests for Optuna hyperparameter optimizer.
"""

import pytest
import numpy as np
from sklearn.datasets import make_classification


@pytest.fixture(scope="module")
def sample_data():
    X, y = make_classification(
        n_samples=200,
        n_features=10,
        n_informative=5,
        random_state=42,
    )
    return X.astype(np.float32), y


class TestOptunaOptimizerImport:
    def test_class_importable(self):
        from app.training.optuna_optimizer import OptunaOptimizer

        optimizer = OptunaOptimizer(model_type="xgboost", n_trials=10)
        assert optimizer.model_type == "xgboost"
        assert optimizer.n_trials == 10
        assert optimizer.study is None
        assert optimizer.best_params is None

    def test_import_error_when_optuna_missing(self):
        from app.training.optuna_optimizer import OptunaOptimizer
        import importlib

        spec = importlib.util.find_spec("optuna")
        if spec is not None:
            pytest.skip("optuna is installed - cannot test without-optuna path")

        opt = OptunaOptimizer()
        with pytest.raises(ImportError, match="optuna is required"):
            opt.optimize(None, None, None, None)


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("optuna"),
    reason="optuna not installed",
)
class TestOptunaOptimizerSuggestParams:
    def test_suggest_xgboost_params(self):
        from app.training.optuna_optimizer import OptunaOptimizer
        import optuna

        optimizer = OptunaOptimizer(model_type="xgboost")

        study = optuna.create_study(direction="maximize")
        trial = study.ask()

        params = optimizer._suggest_params(trial)
        assert "n_estimators" in params
        assert "max_depth" in params
        assert "learning_rate" in params
        assert "subsample" in params
        assert "colsample_bytree" in params
        assert "reg_alpha" in params
        assert "reg_lambda" in params

        assert 100 <= params["n_estimators"] <= 2000
        assert params["n_estimators"] % 100 == 0
        assert 3 <= params["max_depth"] <= 12
        assert 0.01 <= params["learning_rate"] <= 0.3
        assert 0.5 <= params["subsample"] <= 1.0
        assert 0.5 <= params["colsample_bytree"] <= 1.0

    def test_suggest_lightgbm_params(self):
        from app.training.optuna_optimizer import OptunaOptimizer
        import optuna

        optimizer = OptunaOptimizer(model_type="lightgbm")

        study = optuna.create_study(direction="maximize")
        trial = study.ask()

        params = optimizer._suggest_params(trial)
        assert "n_estimators" in params
        assert "num_leaves" in params
        assert "learning_rate" in params
        assert "subsample" in params
        assert "colsample_bytree" in params
        assert "min_child_samples" in params

        assert 16 <= params["num_leaves"] <= 256
        assert 5 <= params["min_child_samples"] <= 100

    def test_suggest_catboost_params(self):
        from app.training.optuna_optimizer import OptunaOptimizer
        import optuna

        optimizer = OptunaOptimizer(model_type="catboost")

        study = optuna.create_study(direction="maximize")
        trial = study.ask()

        params = optimizer._suggest_params(trial)
        assert "iterations" in params
        assert "depth" in params
        assert "learning_rate" in params
        assert "l2_leaf_reg" in params
        assert "border_count" in params

        assert 4 <= params["depth"] <= 10
        assert 32 <= params["border_count"] <= 255

    def test_unknown_model_type(self):
        from app.training.optuna_optimizer import OptunaOptimizer
        import optuna

        optimizer = OptunaOptimizer(model_type="unknown")
        study = optuna.create_study(direction="maximize")
        trial = study.ask()

        with pytest.raises(ValueError, match="unknown"):
            optimizer._suggest_params(trial)

    def test_unknown_model_type_create(self):
        from app.training.optuna_optimizer import OptunaOptimizer

        optimizer = OptunaOptimizer(model_type="unknown")
        with pytest.raises(ValueError, match="unknown"):
            optimizer._create_model({})


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("optuna"),
    reason="optuna not installed",
)
class TestOptunaOptimizerRun:
    def test_optimize_xgboost(self, sample_data):
        from app.training.optuna_optimizer import OptunaOptimizer

        X, y = sample_data
        n = len(X)
        X_train, y_train = X[:120], y[:120]
        X_val, y_val = X[120:], y[120:]

        optimizer = OptunaOptimizer(
            model_type="xgboost",
            n_trials=3,
            timeout=120,
        )
        best_params = optimizer.optimize(X_train, y_train, X_val, y_val)

        assert best_params is not None
        assert "n_estimators" in best_params
        assert "max_depth" in best_params
        assert optimizer.study is not None
        assert optimizer.best_params == best_params

    def test_optimize_lightgbm(self, sample_data):
        from app.training.optuna_optimizer import OptunaOptimizer

        X, y = sample_data
        n = len(X)
        X_train, y_train = X[:120], y[:120]
        X_val, y_val = X[120:], y[120:]

        optimizer = OptunaOptimizer(
            model_type="lightgbm",
            n_trials=3,
            timeout=120,
        )
        best_params = optimizer.optimize(X_train, y_train, X_val, y_val)

        assert best_params is not None
        assert "n_estimators" in best_params
        assert "num_leaves" in best_params

    def test_get_trial_history(self):
        from app.training.optuna_optimizer import OptunaOptimizer

        optimizer = OptunaOptimizer()
        assert optimizer.get_trial_history() == []

    def test_create_model(self, sample_data):
        from app.training.optuna_optimizer import OptunaOptimizer

        X, y = sample_data
        params = {
            "n_estimators": 100,
            "max_depth": 5,
            "learning_rate": 0.1,
        }

        optimizer = OptunaOptimizer(model_type="xgboost")
        model = optimizer._create_model(params)
        assert model is not None
        model.train(X, y)
        preds = model.predict(X[:5])
        assert len(preds) == 5
