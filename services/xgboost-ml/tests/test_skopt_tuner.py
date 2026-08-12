"""
Tests for the scikit-optimize gp_minimize hyperparameter tuner.
"""

import importlib
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


class TestSkoptTunerImport:
    def test_class_importable(self):
        from app.training.skopt_tuner import SkoptTuner

        tuner = SkoptTuner(model_type="xgboost", n_calls=10)
        assert tuner.model_type == "xgboost"
        assert tuner.n_calls == 10
        assert tuner.result is None
        assert tuner.best_params is None
        assert tuner.best_value is None

    def test_import_error_when_skopt_missing(self):
        from app.training.skopt_tuner import SkoptTuner

        spec = importlib.util.find_spec("skopt")
        if spec is not None:
            pytest.skip("scikit-optimize is installed - cannot test without-skopt path")

        tuner = SkoptTuner()
        with pytest.raises(ImportError, match="scikit-optimize is required"):
            tuner.optimize(None, None, None, None)


@pytest.mark.skipif(
    not importlib.util.find_spec("skopt"),
    reason="scikit-optimize not installed",
)
class TestSkoptTunerSpace:
    def test_space_xgboost(self):
        from app.training.skopt_tuner import SkoptTuner

        tuner = SkoptTuner(model_type="xgboost")
        dims = tuner._space()
        names = [d.name for d in dims]
        assert "n_estimators" in names
        assert "max_depth" in names
        assert "learning_rate" in names
        assert "reg_alpha" in names
        assert "reg_lambda" in names
        assert "num_leaves" not in names

    def test_space_lightgbm(self):
        from app.training.skopt_tuner import SkoptTuner

        tuner = SkoptTuner(model_type="lightgbm")
        dims = tuner._space()
        names = [d.name for d in dims]
        assert "n_estimators" in names
        assert "num_leaves" in names
        assert "learning_rate" in names
        assert "reg_alpha" in names
        assert "reg_lambda" in names
        assert "max_depth" not in names

    def test_unknown_model_type_space(self):
        from app.training.skopt_tuner import SkoptTuner

        tuner = SkoptTuner(model_type="unknown")
        with pytest.raises(ValueError, match="unknown"):
            tuner._space()

    def test_unknown_model_type_create(self):
        from app.training.skopt_tuner import SkoptTuner

        tuner = SkoptTuner(model_type="unknown")
        with pytest.raises(ValueError, match="unknown"):
            tuner._create_model({})


@pytest.mark.skipif(
    not importlib.util.find_spec("skopt"),
    reason="scikit-optimize not installed",
)
class TestSkoptTunerRun:
    def test_optimize_xgboost(self, sample_data):
        from app.training.skopt_tuner import SkoptTuner

        X, y = sample_data
        X_train, y_train = X[:120], y[:120]
        X_val, y_val = X[120:], y[120:]

        tuner = SkoptTuner(
            model_type="xgboost",
            n_calls=5,
            n_initial_points=3,
        )
        best_params = tuner.optimize(X_train, y_train, X_val, y_val)

        assert best_params is not None
        assert "n_estimators" in best_params
        assert "max_depth" in best_params
        assert "learning_rate" in best_params
        assert "reg_alpha" in best_params
        assert "reg_lambda" in best_params
        assert tuner.result is not None
        assert tuner.best_params == best_params
        assert tuner.best_value is not None
        assert 0.0 <= tuner.best_value <= 1.0

    def test_optimize_lightgbm(self, sample_data):
        from app.training.skopt_tuner import SkoptTuner

        X, y = sample_data
        X_train, y_train = X[:120], y[:120]
        X_val, y_val = X[120:], y[120:]

        tuner = SkoptTuner(
            model_type="lightgbm",
            n_calls=5,
            n_initial_points=3,
        )
        best_params = tuner.optimize(X_train, y_train, X_val, y_val)

        assert best_params is not None
        assert "n_estimators" in best_params
        assert "num_leaves" in best_params
        assert "learning_rate" in best_params
        assert tuner.best_value is not None

    @pytest.mark.skipif(
        not importlib.util.find_spec("optuna"),
        reason="optuna not installed - cannot compare to Optuna baseline",
    )
    def test_auc_comparable_to_optuna(self, sample_data):
        """skopt val AUC must be within -0.01 of the Optuna baseline."""
        from app.training.skopt_tuner import SkoptTuner
        from app.training.optuna_optimizer import OptunaOptimizer

        X, y = sample_data
        X_train, y_train = X[:120], y[:120]
        X_val, y_val = X[120:], y[120:]

        optuna = OptunaOptimizer(model_type="xgboost", n_trials=3, timeout=120)
        optuna.optimize(X_train, y_train, X_val, y_val)
        optuna_auc = optuna.study.best_value

        skopt = SkoptTuner(model_type="xgboost", n_calls=5, n_initial_points=3)
        skopt.optimize(X_train, y_train, X_val, y_val)
        skopt_auc = skopt.best_value

        assert skopt_auc >= optuna_auc - 0.01

    def test_get_trial_history(self):
        from app.training.skopt_tuner import SkoptTuner

        tuner = SkoptTuner()
        assert tuner.get_trial_history() == []

    def test_create_model(self, sample_data):
        from app.training.skopt_tuner import SkoptTuner

        X, y = sample_data
        params = {
            "n_estimators": 100,
            "max_depth": 5,
            "learning_rate": 0.1,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
        }

        tuner = SkoptTuner(model_type="xgboost")
        model = tuner._create_model(params)
        assert model is not None
        model.train(X, y)
        preds = model.predict(X[:5])
        assert len(preds) == 5


@pytest.mark.skipif(
    not importlib.util.find_spec("skopt"),
    reason="scikit-optimize not installed",
)
class TestOptunaUnaffected:
    def test_optuna_still_importable(self):
        """Optuna path remains importable/unaffected by the skopt tuner."""
        from app.training.optuna_optimizer import OptunaOptimizer

        optimizer = OptunaOptimizer(model_type="xgboost", n_trials=3)
        assert optimizer.model_type == "xgboost"
        assert optimizer.n_trials == 3
