"""
Tests for sklearn baseline models.
"""

import os
import sys
import tempfile
import pytest
import numpy as np
from sklearn.datasets import make_classification
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier


@pytest.fixture(scope="module")
def sample_data():
    X, y = make_classification(
        n_samples=200,
        n_features=10,
        n_informative=5,
        random_state=42,
    )
    return X.astype(np.float32), y


class TestSklearnModelWrapper:
    def test_train_and_predict(self, sample_data):
        from app.models.sklearn_models import SklearnModelWrapper

        X, y = sample_data
        estimator = LogisticRegression(max_iter=1000, random_state=42)
        wrapper = SklearnModelWrapper(estimator)

        assert wrapper.trained is False

        metrics = wrapper.train(X[:150], y[:150], X[150:], y[150:])
        assert wrapper.trained is True
        assert "train_accuracy" in metrics
        assert 0 <= metrics["train_accuracy"] <= 1

        preds = wrapper.predict(X[150:])
        assert len(preds) == len(y[150:])
        assert all(0 <= p <= 1 for p in preds)

    def test_predict_single(self, sample_data):
        from app.models.sklearn_models import SklearnModelWrapper

        X, y = sample_data
        wrapper = SklearnModelWrapper(LogisticRegression(max_iter=1000, random_state=42))
        wrapper.train(X, y)

        result = wrapper.predict_single(X[0])
        assert isinstance(result, float)
        assert 0 <= result <= 1

    def test_predict_without_proba(self, sample_data):
        from app.models.sklearn_models import SklearnModelWrapper
        from sklearn.svm import SVC

        X, y = sample_data
        estimator = SVC(probability=False, random_state=42)
        wrapper = SklearnModelWrapper(estimator)
        wrapper.train(X, y)

        preds = wrapper.predict(X[:5])
        assert len(preds) == 5

    def test_save_and_load(self, sample_data):
        from app.models.sklearn_models import SklearnModelWrapper

        X, y = sample_data
        wrapper = SklearnModelWrapper(LogisticRegression(max_iter=1000, random_state=42))
        wrapper.train(X, y)
        original_preds = wrapper.predict(X[:5])

        with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as f:
            tmp_path = f.name
        try:
            wrapper.save(tmp_path)

            wrapper2 = SklearnModelWrapper(LogisticRegression(max_iter=1000, random_state=42))
            wrapper2.load(tmp_path)
            assert wrapper2.trained is True

            loaded_preds = wrapper2.predict(X[:5])
            np.testing.assert_array_almost_equal(original_preds, loaded_preds)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_feature_importance_coef(self, sample_data):
        from app.models.sklearn_models import SklearnModelWrapper

        X, y = sample_data
        wrapper = SklearnModelWrapper(LogisticRegression(max_iter=1000, random_state=42))
        wrapper.train(X, y)

        importance = wrapper.feature_importance()
        assert importance is not None
        assert len(importance) == X.shape[1]

    def test_feature_importance_tree(self, sample_data):
        from app.models.sklearn_models import SklearnModelWrapper

        X, y = sample_data
        wrapper = SklearnModelWrapper(RandomForestClassifier(n_estimators=10, random_state=42))
        wrapper.train(X, y)

        importance = wrapper.feature_importance()
        assert importance is not None
        assert len(importance) == X.shape[1]

    def test_feature_importance_none(self, sample_data):
        from app.models.sklearn_models import SklearnModelWrapper
        from sklearn.neighbors import KNeighborsClassifier

        X, y = sample_data
        wrapper = SklearnModelWrapper(KNeighborsClassifier())
        wrapper.train(X, y)

        assert wrapper.feature_importance() is None


class TestFactoryFunctions:
    def test_create_random_forest(self, sample_data):
        from app.models.sklearn_models import create_random_forest

        X, y = sample_data
        wrapper = create_random_forest(n_estimators=50, max_depth=5)
        assert wrapper.trained is False

        metrics = wrapper.train(X, y)
        assert metrics["train_accuracy"] > 0

        preds = wrapper.predict(X[:5])
        assert len(preds) == 5

        importance = wrapper.feature_importance()
        assert importance is not None

    def test_create_logistic_regression(self, sample_data):
        from app.models.sklearn_models import create_logistic_regression

        X, y = sample_data
        wrapper = create_logistic_regression(C=0.5)
        metrics = wrapper.train(X, y)
        assert metrics["train_accuracy"] > 0

        preds = wrapper.predict(X[:5])
        assert len(preds) == 5

        importance = wrapper.feature_importance()
        assert importance is not None

    def test_create_svm(self, sample_data):
        from app.models.sklearn_models import create_svm

        X, y = sample_data
        wrapper = create_svm(kernel="rbf", C=1.0)
        metrics = wrapper.train(X, y)
        assert metrics["train_accuracy"] > 0

        preds = wrapper.predict(X[:5])
        assert len(preds) == 5

    def test_factory_default_params(self):
        from app.models.sklearn_models import (
            create_random_forest,
            create_logistic_regression,
            create_svm,
        )

        rf = create_random_forest()
        assert rf.estimator.n_estimators == 200
        assert rf.estimator.max_depth == 10

        lr = create_logistic_regression()
        assert lr.estimator.C == 1.0

        svm = create_svm()
        assert svm.estimator.kernel == "rbf"
        assert svm.estimator.C == 1.0
