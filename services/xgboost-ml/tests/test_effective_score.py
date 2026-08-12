"""
Tests for the shared effective_score module (Phase 4).

Covers:
- ``compute_effective_score`` composition (effective_score = calibrated_prob - kappa*sigma)
- ``EffectiveScore.score`` happy path (calibrator + GP) and missing-sigma fallback
- screener ``score_and_filter_candidates``: flag on sorts by effective_score and
  gates on it; flag off sorts by raw confidence and gates on raw prob (revertibility)
- backtester ``should_buy``: flag on gates on effective_score >= 0.65; flag off
  gates on prob >= 0.65 (revertibility)
"""

import logging

import numpy as np
import pytest
from sklearn.datasets import make_regression

from app.scoring.effective_score import (
    DEFAULT_KAPPA,
    EffectiveScore,
    compute_effective_score,
    score_and_filter_candidates,
    should_buy,
)
from app.uncertainty.gp_uncertainty import GPUncertainty, LOW_DIM_FEATURES


class _FakeCalibrator:
    """Minimal calibrator exposing ``calibrate`` -> calibrated_probability."""

    def __init__(self, calibrated_probability):
        self._cal = calibrated_probability

    def calibrate(self, probs):
        return {"calibrated_probability": self._cal, "calibration_uncertainty": 0.01}


class _FakeScorer:
    """Minimal scorer exposing ``score`` -> a fixed effective_score dict."""

    def __init__(self, effective, calibrated, sigma):
        self._effective = effective
        self._calibrated = calibrated
        self._sigma = sigma

    def score(self, prob, feature_vec=None, sigma=None):
        return {
            "effective_score": self._effective,
            "calibrated_probability": self._calibrated,
            "epistemic_std": self._sigma,
        }


@pytest.fixture(scope="module")
def fitted_gp():
    """A real fitted GPUncertainty on synthetic low-dim data."""
    n_features = len(LOW_DIM_FEATURES)
    X, y = make_regression(
        n_samples=120, n_features=n_features, n_informative=n_features,
        noise=0.1, random_state=42,
    )
    gp = GPUncertainty()
    gp.fit(X.astype(np.float64), y.astype(np.float64))
    return gp


class TestComputeEffectiveScore:
    def test_composition(self):
        assert compute_effective_score(0.7, 0.1, kappa=0.3) == pytest.approx(0.67)
        assert compute_effective_score(0.7, 0.1, kappa=0.0) == pytest.approx(0.7)
        assert compute_effective_score(0.7, 0.5, kappa=0.3) < compute_effective_score(
            0.7, 0.1, kappa=0.3
        )

    def test_default_kappa_is_0_3(self):
        # κ-sweep (Todo 3) showed κ=0.3 gives best Sharpe.
        assert DEFAULT_KAPPA == 0.3


class TestEffectiveScoreHappyPath:
    def test_score_with_calibrator_and_gp(self, fitted_gp):
        cal = _FakeCalibrator(0.7)
        scorer = EffectiveScore(calibrator=cal, gp=fitted_gp, kappa=0.3)
        vec = np.zeros(len(LOW_DIM_FEATURES))
        result = scorer.score(0.8, feature_vec=vec)
        assert result["calibrated_probability"] == pytest.approx(0.7)
        assert result["epistemic_std"] is not None
        assert result["effective_score"] == pytest.approx(
            0.7 - 0.3 * result["epistemic_std"]
        )

    def test_score_without_calibrator_uses_raw_prob(self, fitted_gp):
        scorer = EffectiveScore(gp=fitted_gp, kappa=0.3)
        vec = np.zeros(len(LOW_DIM_FEATURES))
        result = scorer.score(0.8, feature_vec=vec)
        assert result["calibrated_probability"] == pytest.approx(0.8)

    def test_score_with_explicit_sigma_skips_gp(self):
        scorer = EffectiveScore(kappa=0.3)
        result = scorer.score(0.7, sigma=0.1)
        assert result["effective_score"] == pytest.approx(0.67)
        assert result["epistemic_std"] == pytest.approx(0.1)


class TestEffectiveScoreMissingSigmaFallback:
    def test_missing_sigma_falls_back_to_calibrated_prob(self, caplog):
        cal = _FakeCalibrator(0.7)
        # No GP -> sigma missing -> fall back to calibrated_prob, warn, not crash.
        scorer = EffectiveScore(calibrator=cal, gp=None)
        with caplog.at_level(logging.WARNING):
            result = scorer.score(0.8)
        assert result["effective_score"] == pytest.approx(0.7)
        assert result["calibrated_probability"] == pytest.approx(0.7)
        assert result["epistemic_std"] is None
        assert any("GPR unavailable" in r.message for r in caplog.records)

    def test_missing_sigma_no_calibrator_falls_back_to_raw_prob(self, caplog):
        scorer = EffectiveScore(calibrator=None, gp=None)
        with caplog.at_level(logging.WARNING):
            result = scorer.score(0.8)
        assert result["effective_score"] == pytest.approx(0.8)
        assert result["epistemic_std"] is None


class TestScreenerScoreAndFilter:
    def _raw(self):
        return [
            {"stock_code": "A", "stock_name": "A", "sector": "S",
             "prob": 0.9, "low_dim_vec": None},
            {"stock_code": "B", "stock_name": "B", "sector": "S",
             "prob": 0.6, "low_dim_vec": None},
            {"stock_code": "C", "stock_name": "C", "sector": "S",
             "prob": 0.5, "low_dim_vec": None},
        ]

    def test_flag_on_sorts_by_effective_score_and_gates_on_it(self):
        # effective scores: A=0.8, B=0.7, C=0.4 (C below threshold 0.55).
        by_code = {"A": 0.8, "B": 0.7, "C": 0.4}

        class _PerCode:
            def score(self, prob, feature_vec=None, sigma=None):
                code = feature_vec
                return {
                    "effective_score": by_code[code],
                    "calibrated_probability": prob,
                    "epistemic_std": 0.1,
                }

        raw = [
            {"stock_code": "A", "stock_name": "A", "sector": "S", "prob": 0.9,
             "low_dim_vec": "A"},
            {"stock_code": "B", "stock_name": "B", "sector": "S", "prob": 0.6,
             "low_dim_vec": "B"},
            {"stock_code": "C", "stock_name": "C", "sector": "S", "prob": 0.5,
             "low_dim_vec": "C"},
        ]
        out = score_and_filter_candidates(raw, _PerCode(), True, 0.55)
        # C (0.4) filtered out; A (0.8) and B (0.7) kept, sorted by effective desc.
        assert [c["stock_code"] for c in out] == ["A", "B"]
        assert out[0]["effective_score"] == 0.8
        assert out[1]["effective_score"] == 0.7
        assert "calibrated_probability" in out[0]
        assert "epistemic_std" in out[0]

    def test_flag_off_sorts_by_confidence_and_gates_on_raw_prob(self):
        raw = self._raw()
        out = score_and_filter_candidates(raw, None, False, 0.55)
        # prob >= 0.55: A(0.9), B(0.6); C(0.5) filtered. Sorted by confidence desc.
        assert [c["stock_code"] for c in out] == ["A", "B"]
        assert out[0]["confidence"] == 0.9
        assert "effective_score" not in out[0]
        assert "calibrated_probability" not in out[0]

    def test_flag_off_revertibility_matches_old_behavior(self):
        # Old behavior: filter prob >= threshold, sort by confidence desc.
        raw = self._raw()
        out = score_and_filter_candidates(raw, None, False, 0.55)
        expected = sorted(
            [c for c in raw if c["prob"] >= 0.55],
            key=lambda x: x["prob"], reverse=True,
        )
        assert [c["stock_code"] for c in out] == [c["stock_code"] for c in expected]


class TestBacktesterShouldBuy:
    def test_flag_on_gates_on_effective_score(self):
        # effective_score >= 0.65 -> buy; below -> no.
        assert should_buy(0.9, 0.7, True) is True
        assert should_buy(0.9, 0.5, True) is False
        # raw prob high but effective low -> no buy (uncertainty penalty).
        assert should_buy(0.9, 0.6, True) is False

    def test_flag_off_gates_on_raw_prob(self):
        assert should_buy(0.7, None, False) is True
        assert should_buy(0.6, None, False) is False

    def test_flag_off_revertibility(self):
        # Old behavior: gate on prob >= 0.65 regardless of effective_score.
        assert should_buy(0.65, 0.0, False) is True
        assert should_buy(0.64, 0.9, False) is False

    def test_flag_on_missing_effective_score_no_buy(self):
        assert should_buy(0.9, None, True) is False
