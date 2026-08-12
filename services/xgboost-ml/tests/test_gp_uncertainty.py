"""
Tests for the GP uncertainty module (Phase 2).

Covers:
- happy path: sigma is finite for all rows after an offline fit
- failure path: high-dimensional input raises a clear error
- low-dim path: predict_std returns a finite float for a single vector
- effective_score composition and batch prediction
"""

import numpy as np
import pytest
from sklearn.datasets import make_regression

from app.uncertainty.gp_uncertainty import (
    GPUncertainty,
    LOW_DIM_FEATURES,
    MAX_FEATURES,
)


@pytest.fixture(scope="module")
def low_dim_data():
    """Synthetic low-dim feature matrix + 5-day return target."""
    n_features = len(LOW_DIM_FEATURES)
    X, y = make_regression(
        n_samples=120,
        n_features=n_features,
        n_informative=n_features,
        noise=0.1,
        random_state=42,
    )
    return X.astype(np.float64), y.astype(np.float64)


class TestGPUncertaintyImport:
    def test_module_importable(self):
        from app.uncertainty.gp_uncertainty import GPUncertainty

        gp = GPUncertainty()
        assert gp.n_features == len(LOW_DIM_FEATURES)
        assert gp.is_fitted is False

    def test_low_dim_feature_subset_is_small(self):
        # Guard: the GP must use a LOW-dimensional subset, not all 42/43 features.
        assert len(LOW_DIM_FEATURES) <= MAX_FEATURES
        assert len(LOW_DIM_FEATURES) < 15
        # Must include momentum/volume/kalman features.
        assert "return_5d" in LOW_DIM_FEATURES
        assert "return_20d" in LOW_DIM_FEATURES
        assert "volume_ratio_5" in LOW_DIM_FEATURES
        assert "kalman_momentum_1d" in LOW_DIM_FEATURES
        assert "kalman_volatility" in LOW_DIM_FEATURES


class TestGPUncertaintyHappyPath:
    def test_fit_and_predict_std_finite_for_all_rows(self, low_dim_data):
        X, y = low_dim_data
        gp = GPUncertainty()
        gp.fit(X, y)
        assert gp.is_fitted is True

        sigmas = gp.predict_std_batch(X)
        assert sigmas.shape == (X.shape[0],)
        assert np.all(np.isfinite(sigmas))
        assert np.all(sigmas >= 0.0)

    def test_predict_std_single_vector_finite(self, low_dim_data):
        X, y = low_dim_data
        gp = GPUncertainty()
        gp.fit(X, y)

        sigma = gp.predict_std(X[0])
        assert isinstance(sigma, float)
        assert np.isfinite(sigma)
        assert sigma >= 0.0

    def test_effective_score_composition(self):
        gp = GPUncertainty()
        # effective_score = calibrated_prob - kappa * sigma
        assert gp.effective_score(0.7, 0.1, kappa=0.2) == pytest.approx(0.68)
        # kappa=0 => no penalty
        assert gp.effective_score(0.7, 0.1, kappa=0.0) == pytest.approx(0.7)
        # larger sigma => lower score
        assert gp.effective_score(0.7, 0.5, kappa=0.2) < gp.effective_score(
            0.7, 0.1, kappa=0.2
        )

    def test_batch_prediction_matches_single(self, low_dim_data):
        X, y = low_dim_data
        gp = GPUncertainty()
        gp.fit(X, y)

        batch = gp.predict_std_batch(X[:5])
        singles = np.array([gp.predict_std(X[i]) for i in range(5)])
        np.testing.assert_allclose(batch, singles, rtol=1e-6)


class TestGPUncertaintyFailurePath:
    def test_high_dim_fit_raises(self):
        # 42 features (champion-like) must be rejected.
        X_high, y_high = make_regression(
            n_samples=60, n_features=42, random_state=7
        )
        gp = GPUncertainty()
        with pytest.raises(ValueError, match="high-dimensional|expects"):
            gp.fit(X_high, y_high)

    def test_high_dim_constructor_raises(self):
        # Passing > MAX_FEATURES feature names must raise at construction.
        too_many = [f"f{i}" for i in range(MAX_FEATURES + 1)]
        with pytest.raises(ValueError, match="LOW-dimensional"):
            GPUncertainty(feature_names=too_many)

    def test_predict_std_before_fit_raises(self):
        gp = GPUncertainty()
        with pytest.raises(ValueError, match="not fitted"):
            gp.predict_std([0.0] * len(LOW_DIM_FEATURES))

    def test_predict_std_wrong_width_raises(self, low_dim_data):
        X, y = low_dim_data
        gp = GPUncertainty()
        gp.fit(X, y)
        # Wrong width (e.g. 42 features) must raise.
        with pytest.raises(ValueError, match="expects|unsupported"):
            gp.predict_std([0.0] * 42)

    def test_fit_wrong_width_raises(self, low_dim_data):
        X, y = low_dim_data
        gp = GPUncertainty()
        # 2D but wrong column count (e.g. 42) must raise.
        X_bad = np.zeros((10, 42))
        with pytest.raises(ValueError, match="expects|high-dimensional"):
            gp.fit(X_bad, y[:10])


class TestGPUncertaintyLowDimPath:
    def test_low_dim_returns_finite_sigma(self, low_dim_data):
        X, y = low_dim_data
        gp = GPUncertainty()
        gp.fit(X, y)

        # A clean low-dim vector (all zeros) must still return finite sigma.
        vec = np.zeros(len(LOW_DIM_FEATURES))
        sigma = gp.predict_std(vec)
        assert np.isfinite(sigma)
        assert sigma >= 0.0

    def test_nan_input_handled(self, low_dim_data):
        X, y = low_dim_data
        gp = GPUncertainty()
        gp.fit(X, y)

        vec = np.full(len(LOW_DIM_FEATURES), np.nan)
        sigma = gp.predict_std(vec)
        assert np.isfinite(sigma)
