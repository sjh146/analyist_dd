"""
Tests for sklearn pipeline.
"""

import os
import tempfile
import pytest
import numpy as np
from sklearn.datasets import make_classification
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier


@pytest.fixture(scope="module")
def sample_data():
    X, y = make_classification(
        n_samples=300,
        n_features=15,
        n_informative=8,
        random_state=42,
    )
    return X.astype(np.float32), y


class TestSklearnPipeline:
    def test_create_pipeline(self):
        from app.models.sklearn_pipeline import SklearnPipeline

        estimator = LogisticRegression(max_iter=1000, random_state=42)
        pipeline = SklearnPipeline.create_pipeline(estimator)

        assert pipeline.trained is False
        assert "scaler" in pipeline.pipeline.named_steps
        assert "pca" in pipeline.pipeline.named_steps
        assert "estimator" in pipeline.pipeline.named_steps

    def test_train_and_predict(self, sample_data):
        from app.models.sklearn_pipeline import SklearnPipeline

        X, y = sample_data
        estimator = LogisticRegression(max_iter=1000, random_state=42)
        pipeline = SklearnPipeline.create_pipeline(estimator)

        metrics = pipeline.train(X[:200], y[:200], X[200:], y[200:])
        assert pipeline.trained is True
        assert "train_accuracy" in metrics
        assert 0 <= metrics["train_accuracy"] <= 1

        preds = pipeline.predict(X[200:])
        assert len(preds) == len(y[200:])
        assert all(0 <= p <= 1 for p in preds)

    def test_predict_single(self, sample_data):
        from app.models.sklearn_pipeline import SklearnPipeline

        X, y = sample_data
        pipeline = SklearnPipeline.create_pipeline(
            LogisticRegression(max_iter=1000, random_state=42)
        )
        pipeline.train(X, y)

        result = pipeline.predict_single(X[0])
        assert isinstance(result, float)
        assert 0 <= result <= 1

    def test_save_and_load(self, sample_data):
        from app.models.sklearn_pipeline import SklearnPipeline

        X, y = sample_data
        pipeline = SklearnPipeline.create_pipeline(
            LogisticRegression(max_iter=1000, random_state=42)
        )
        pipeline.train(X, y)
        original_preds = pipeline.predict(X[:5])

        with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as f:
            tmp_path = f.name
        try:
            pipeline.save(tmp_path)

            pipeline2 = SklearnPipeline.create_pipeline(
                LogisticRegression(max_iter=1000, random_state=42)
            )
            pipeline2.load(tmp_path)
            assert pipeline2.trained is True

            loaded_preds = pipeline2.predict(X[:5])
            np.testing.assert_array_almost_equal(original_preds, loaded_preds)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_feature_importance(self, sample_data):
        from app.models.sklearn_pipeline import SklearnPipeline

        X, y = sample_data
        pipeline = SklearnPipeline.create_pipeline(
            LogisticRegression(max_iter=1000, random_state=42)
        )
        pipeline.train(X, y)

        importance = pipeline.feature_importance()
        assert importance is not None

    def test_feature_importance_tree(self, sample_data):
        from app.models.sklearn_pipeline import SklearnPipeline

        X, y = sample_data
        pipeline = SklearnPipeline.create_pipeline(
            RandomForestClassifier(n_estimators=10, random_state=42)
        )
        pipeline.train(X, y)

        importance = pipeline.feature_importance()
        assert importance is not None
        assert len(importance) == pipeline.pipeline.named_steps["pca"].n_components_

    def test_pca_components(self, sample_data):
        from app.models.sklearn_pipeline import SklearnPipeline

        X, y = sample_data
        pipeline = SklearnPipeline.create_pipeline(
            LogisticRegression(max_iter=1000, random_state=42)
        )
        pipeline.train(X, y)

        var = pipeline.get_pca_explained_variance()
        assert var is not None
        assert sum(var) >= 0.9

        n = pipeline.get_pca_n_components()
        assert n > 0
        assert n <= X.shape[1]

    def test_predict_without_proba(self, sample_data):
        from app.models.sklearn_pipeline import SklearnPipeline
        from sklearn.svm import SVC

        X, y = sample_data
        estimator = SVC(probability=False, random_state=42)
        pipeline = SklearnPipeline.create_pipeline(estimator)
        pipeline.train(X, y)

        preds = pipeline.predict(X[:5])
        assert len(preds) == 5

    def test_different_estimator(self, sample_data):
        from app.models.sklearn_pipeline import SklearnPipeline
        from sklearn.ensemble import GradientBoostingClassifier

        X, y = sample_data
        estimator = GradientBoostingClassifier(n_estimators=50, random_state=42)
        pipeline = SklearnPipeline.create_pipeline(estimator)
        metrics = pipeline.train(X, y)

        assert metrics["train_accuracy"] > 0
        preds = pipeline.predict(X[:5])
        assert all(0 <= p <= 1 for p in preds)
