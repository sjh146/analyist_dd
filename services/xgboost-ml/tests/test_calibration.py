"""
Tests for the probability calibration layer.

Covers the deterministic Platt/isotonic wrappers and the NumPyro Bayesian
calibrator: importability, happy path (calibrated_probability in [0,1]),
failure path (misordered/out-of-range probs raise), raw-ensemble passthrough,
and a walk-forward ECE / AUC regression check.
"""

import importlib.util

import numpy as np
import pytest
from sklearn.datasets import make_classification
from sklearn.metrics import roc_auc_score

NUMPYRO_AVAILABLE = importlib.util.find_spec("numpyro") is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ece(y_true, probs, n_bins=10):
    """Expected Calibration Error over a fixed binning of [0, 1]."""
    y_true = np.asarray(y_true, dtype=np.float64)
    probs = np.asarray(probs, dtype=np.float64)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    bin_ids = np.clip(np.digitize(probs, bins[1:-1]), 0, n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        mask = bin_ids == b
        if mask.sum() == 0:
            continue
        conf = probs[mask].mean()
        acc = y_true[mask].mean()
        ece += (mask.sum() / len(probs)) * abs(conf - acc)
    return ece


def _miscalibrated_data(n=600, seed=42, shift=0.8):
    """Return (raw_probs, y) where raw_probs are overconfident vs truth.

    Labels are drawn from a well-separated logistic score, then the raw
    probabilities are shifted in logit space (``+shift``) so they are
    systematically overconfident. This is the classic Platt-scaling scenario:
    calibration should restore the probabilities without hurting ranking.
    """
    rng = np.random.default_rng(seed)
    X, _ = make_classification(
        n_samples=n,
        n_features=8,
        n_informative=5,
        n_redundant=2,
        random_state=seed,
    )
    w = rng.normal(0.0, 1.0, X.shape[1])
    score = X @ w
    true_prob = 1.0 / (1.0 + np.exp(-score))
    y = (rng.uniform(0.0, 1.0, n) < true_prob).astype(int)
    raw = np.clip(1.0 / (1.0 + np.exp(-(score + shift))), 0.02, 0.98)
    return raw, y


@pytest.fixture(scope="module")
def sample_data():
    return _miscalibrated_data()


# ---------------------------------------------------------------------------
# Importability
# ---------------------------------------------------------------------------
class TestCalibrationImports:
    def test_platt_importable(self):
        from app.calibration.platt import PlattCalibrator

        assert PlattCalibrator is not None

    def test_isotonic_importable(self):
        from app.calibration.isotonic import IsotonicCalibrator

        assert IsotonicCalibrator is not None

    def test_bayesian_importable(self):
        from app.calibration.bayesian_calibration import BayesianCalibrator

        assert BayesianCalibrator is not None

    def test_package_exports(self):
        from app.calibration import (
            BayesianCalibrator,
            IsotonicCalibrator,
            PlattCalibrator,
        )

        assert PlattCalibrator is not None
        assert IsotonicCalibrator is not None
        assert BayesianCalibrator is not None


# ---------------------------------------------------------------------------
# Deterministic calibrators (Platt / isotonic)
# ---------------------------------------------------------------------------
class TestDeterministicCalibrators:
    def test_platt_happy_path(self, sample_data):
        from app.calibration.platt import PlattCalibrator

        raw, y = sample_data
        cal = PlattCalibrator().fit(raw, y)
        out = cal.calibrate(raw)
        assert np.all(out >= 0.0) and np.all(out <= 1.0)

    def test_platt_scalar(self, sample_data):
        from app.calibration.platt import PlattCalibrator

        raw, y = sample_data
        cal = PlattCalibrator().fit(raw, y)
        out = cal.calibrate(0.7)
        assert isinstance(out, float)
        assert 0.0 <= out <= 1.0

    def test_isotonic_happy_path(self, sample_data):
        from app.calibration.isotonic import IsotonicCalibrator

        raw, y = sample_data
        cal = IsotonicCalibrator().fit(raw, y)
        out = cal.calibrate(raw)
        assert np.all(out >= 0.0) and np.all(out <= 1.0)

    def test_isotonic_scalar(self, sample_data):
        from app.calibration.isotonic import IsotonicCalibrator

        raw, y = sample_data
        cal = IsotonicCalibrator().fit(raw, y)
        out = cal.calibrate(0.7)
        assert isinstance(out, float)
        assert 0.0 <= out <= 1.0

    def test_calibrate_before_fit_raises(self):
        from app.calibration.platt import PlattCalibrator

        cal = PlattCalibrator()
        with pytest.raises(RuntimeError, match="fit"):
            cal.calibrate(0.5)

    def test_misordered_probs_raise(self, sample_data):
        from app.calibration.platt import PlattCalibrator

        raw, y = sample_data
        cal = PlattCalibrator().fit(raw, y)
        # Out-of-range / non-finite values must raise.
        with pytest.raises(ValueError, match="[0, 1]"):
            cal.calibrate(np.array([0.5, 1.5]))
        with pytest.raises(ValueError, match="NaN"):
            cal.calibrate(np.array([0.5, np.nan]))

    def test_mismatched_lengths_raise(self, sample_data):
        from app.calibration.isotonic import IsotonicCalibrator

        raw, y = sample_data
        with pytest.raises(ValueError, match="same length"):
            IsotonicCalibrator().fit(raw[:-1], y)

    def test_non_binary_labels_raise(self, sample_data):
        from app.calibration.platt import PlattCalibrator

        raw, y = sample_data
        bad_y = np.where(y == 1, 2, 0)
        with pytest.raises(ValueError, match="binary"):
            PlattCalibrator().fit(raw, bad_y)


# ---------------------------------------------------------------------------
# Bayesian calibrator (NumPyro)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not NUMPYRO_AVAILABLE,
    reason="numpyro not installed",
)
class TestBayesianCalibrator:
    def test_happy_path(self, sample_data):
        from app.calibration.bayesian_calibration import BayesianCalibrator

        raw, y = sample_data
        cal = BayesianCalibrator(
            num_warmup=200, num_samples=300, num_chains=2
        ).fit(raw, y)
        out = cal.calibrate(raw)
        assert "calibrated_probability" in out
        assert "calibration_uncertainty" in out
        assert np.all(out["calibrated_probability"] >= 0.0)
        assert np.all(out["calibrated_probability"] <= 1.0)
        assert np.all(out["calibration_uncertainty"] >= 0.0)

    def test_scalar_returns_floats(self, sample_data):
        from app.calibration.bayesian_calibration import BayesianCalibrator

        raw, y = sample_data
        cal = BayesianCalibrator(
            num_warmup=200, num_samples=300, num_chains=2
        ).fit(raw, y)
        out = cal.calibrate(0.7)
        assert isinstance(out["calibrated_probability"], float)
        assert isinstance(out["calibration_uncertainty"], float)
        assert 0.0 <= out["calibrated_probability"] <= 1.0

    def test_rhat_recorded(self, sample_data):
        from app.calibration.bayesian_calibration import BayesianCalibrator

        raw, y = sample_data
        cal = BayesianCalibrator(
            num_warmup=200, num_samples=300, num_chains=2
        ).fit(raw, y)
        assert "p0" in cal.rhat
        assert "b" in cal.rhat
        assert cal.converged is True

    def test_calibrate_before_fit_raises(self):
        from app.calibration.bayesian_calibration import BayesianCalibrator

        cal = BayesianCalibrator()
        with pytest.raises(RuntimeError, match="fit"):
            cal.calibrate(0.5)

    def test_misordered_probs_raise(self, sample_data):
        from app.calibration.bayesian_calibration import BayesianCalibrator

        raw, y = sample_data
        cal = BayesianCalibrator(
            num_warmup=200, num_samples=300, num_chains=2
        ).fit(raw, y)
        with pytest.raises(ValueError, match="[0, 1]"):
            cal.calibrate(np.array([0.5, 1.5]))


# ---------------------------------------------------------------------------
# Raw-ensemble passthrough
# ---------------------------------------------------------------------------
class TestRawEnsemblePassthrough:
    def test_ensemble_predict_unchanged(self):
        """EnsembleModel.predict must remain the raw weighted-avg probability."""
        from app.models.ensemble_model import EnsembleModel

        model = EnsembleModel()
        # predict() on an untrained ensemble still returns a valid probability
        # vector (raw weighted average) without error.
        out = model.predict(np.zeros((3, 5)))
        assert out.shape == (3,)
        assert np.all(out >= 0.0) and np.all(out <= 1.0)

    def test_calibration_is_separate_layer(self, sample_data):
        """Calibration must not mutate the raw probs passed in."""
        from app.calibration.platt import PlattCalibrator

        raw, y = sample_data
        raw_copy = raw.copy()
        PlattCalibrator().fit(raw, y).calibrate(raw)
        np.testing.assert_array_equal(raw, raw_copy)


# ---------------------------------------------------------------------------
# Walk-forward: ECE improves and AUC does not regress
# ---------------------------------------------------------------------------
class TestWalkForward:
    def test_walk_forward_ece_and_auc(self):
        """Post-calibration ECE <= pre-calibration ECE and AUC drop <= 0.01."""
        from app.calibration.isotonic import IsotonicCalibrator
        from app.calibration.platt import PlattCalibrator

        raw, y = _miscalibrated_data(n=800, seed=42)
        n = len(raw)
        split = int(n * 0.6)
        raw_train, y_train = raw[:split], y[:split]
        raw_val, y_val = raw[split:], y[split:]

        pre_ece = _ece(y_val, raw_val)
        pre_auc = roc_auc_score(y_val, raw_val)

        for Calibrator in (PlattCalibrator, IsotonicCalibrator):
            cal = Calibrator().fit(raw_train, y_train)
            cal_val = cal.calibrate(raw_val)
            post_ece = _ece(y_val, cal_val)
            post_auc = roc_auc_score(y_val, cal_val)

            assert post_ece <= pre_ece + 1e-9, (
                f"{Calibrator.__name__}: post ECE {post_ece:.4f} > "
                f"pre ECE {pre_ece:.4f}"
            )
            assert post_auc >= pre_auc - 0.01, (
                f"{Calibrator.__name__}: AUC dropped from {pre_auc:.4f} "
                f"to {post_auc:.4f}"
            )

    @pytest.mark.skipif(
        not NUMPYRO_AVAILABLE,
        reason="numpyro not installed",
    )
    def test_walk_forward_bayesian_ece_and_auc(self):
        """Bayesian calibration also improves ECE and preserves AUC."""
        from app.calibration.bayesian_calibration import BayesianCalibrator

        raw, y = _miscalibrated_data(n=600, seed=42)
        n = len(raw)
        split = int(n * 0.6)
        raw_train, y_train = raw[:split], y[:split]
        raw_val, y_val = raw[split:], y[split:]

        pre_ece = _ece(y_val, raw_val)
        pre_auc = roc_auc_score(y_val, raw_val)

        cal = BayesianCalibrator(
            num_warmup=200, num_samples=300, num_chains=2
        ).fit(raw_train, y_train)
        out = cal.calibrate(raw_val)
        cal_val = out["calibrated_probability"]

        post_ece = _ece(y_val, cal_val)
        post_auc = roc_auc_score(y_val, cal_val)

        assert post_ece <= pre_ece + 1e-9, (
            f"Bayesian: post ECE {post_ece:.4f} > pre ECE {pre_ece:.4f}"
        )
        assert post_auc >= pre_auc - 0.01, (
            f"Bayesian: AUC dropped from {pre_auc:.4f} to {post_auc:.4f}"
        )
