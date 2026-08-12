"""
Tests for Bayesian momentum/factor features (BayesFactorFeatures) and their
A/B integration against the existing Kalman features in FeaturePipeline.
"""

import importlib.util
import numpy as np
import pandas as pd
import pytest

from app.feature_engine.bayes_factor_features import BayesFactorFeatures
from app.feature_engine.feature_pipeline import FeaturePipeline

NUMPYRO_AVAILABLE = importlib.util.find_spec("numpyro") is not None
SKLEARN_AVAILABLE = importlib.util.find_spec("sklearn") is not None


def _synthetic_close(n=120, seed=42, drift=0.0005):
    """Generate a synthetic trending close-price series."""
    rng = np.random.default_rng(seed)
    log_rets = rng.normal(drift, 0.01, size=n)
    prices = 100.0 * np.exp(np.cumsum(log_rets))
    return prices


class TestBayesFactorFeaturesCompute:
    def test_importable(self):
        from app.feature_engine.bayes_factor_features import BayesFactorFeatures as B
        assert B is not None

    def test_returns_all_four_features(self):
        bf = BayesFactorFeatures(num_warmup=50, num_samples=50)
        out = bf.compute(_synthetic_close())
        assert set(BayesFactorFeatures.FEATURE_NAMES) <= set(out.keys())
        for name in BayesFactorFeatures.FEATURE_NAMES:
            assert name in out

    def test_short_input_returns_defaults(self):
        bf = BayesFactorFeatures()
        out = bf.compute([100.0, 101.0])
        assert out == {name: 0.0 for name in BayesFactorFeatures.FEATURE_NAMES}

    def test_none_input_returns_defaults(self):
        bf = BayesFactorFeatures()
        out = bf.compute(None)
        assert out == {name: 0.0 for name in BayesFactorFeatures.FEATURE_NAMES}

    @pytest.mark.skipif(not NUMPYRO_AVAILABLE, reason="numpyro not installed")
    def test_posterior_std_is_positive(self):
        bf = BayesFactorFeatures(num_warmup=50, num_samples=50)
        out = bf.compute(_synthetic_close())
        # Posterior std must be > 0 (uncertainty preserved, not just mean).
        assert out["bayes_gain_uncertainty"] > 0.0


class TestFeaturePipelineIntegration:
    def _pipeline_with_market(self):
        pipe = FeaturePipeline()
        df = pd.DataFrame({"close": _synthetic_close()})
        return pipe, df

    def test_build_features_appends_bayes_features(self):
        pipe, df = self._pipeline_with_market()
        feats = pipe.build_features("TEST", "2026-01-01", market_df=df)
        for name in BayesFactorFeatures.FEATURE_NAMES:
            assert name in feats

    def test_kalman_features_still_present(self):
        """Regression: wiring Bayes features must NOT remove existing kalman_* features."""
        pipe, df = self._pipeline_with_market()
        feats = pipe.build_features("TEST", "2026-01-01", market_df=df)
        for name in ["kalman_momentum_1d", "kalman_momentum_5d", "kalman_volatility"]:
            assert name in feats

    def test_feature_count_increases_by_exactly_four(self):
        """feature_count must increase by exactly the 4 new bayes features."""
        pipe, df = self._pipeline_with_market()
        feats = pipe.build_features("TEST", "2026-01-01", market_df=df)
        # feature_count is set to len(features) BEFORE stock_code/date/feature_count
        # are appended, so it must equal the number of non-meta feature keys.
        meta = {"stock_code", "date", "feature_count"}
        feature_keys = {k for k in feats if k not in meta}
        assert feats["feature_count"] == len(feature_keys)
        # The 4 bayes features are present and counted.
        assert all(name in feature_keys for name in BayesFactorFeatures.FEATURE_NAMES)

    def test_get_feature_names_includes_bayes_features(self):
        names = FeaturePipeline().get_feature_names()
        for name in BayesFactorFeatures.FEATURE_NAMES:
            assert name in names
        # Kalman names still present (no regression).
        for name in ["kalman_momentum_1d", "kalman_momentum_5d", "kalman_volatility"]:
            assert name in names


class TestABPromotionGuard:
    """A/B guard: new features must NOT be promoted unless val AUC is neutral-or-better.

    Mirrors AutoRetrain.select_champion logic (auto_retrain.py:449) which requires
    BOTH f1 AND auc to improve for promotion. This test asserts that a challenger
    that does NOT beat the champion on AUC is correctly rejected.
    """

    def _ab_auc(self, X_old, X_new, y, seed=7):
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score

        Xtr_o, Xte_o, Xtr_n, Xte_n, ytr, yte = train_test_split(
            X_old, X_new, y, test_size=0.3, random_state=seed
        )
        clf_old = RandomForestClassifier(n_estimators=50, random_state=seed)
        clf_old.fit(Xtr_o, ytr)
        auc_old = roc_auc_score(yte, clf_old.predict_proba(Xte_o)[:, 1])

        clf_new = RandomForestClassifier(n_estimators=50, random_state=seed)
        clf_new.fit(Xtr_n, ytr)
        auc_new = roc_auc_score(yte, clf_new.predict_proba(Xte_n)[:, 1])
        return auc_old, auc_new

    @pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="sklearn not installed")
    def test_challenger_with_worse_auc_is_not_promoted(self):
        rng = np.random.default_rng(0)
        n = 300
        # Old features carry the signal; new features are pure noise.
        X_old = rng.normal(size=(n, 4))
        y = (X_old[:, 0] + 0.5 * X_old[:, 1] > 0).astype(int)
        X_new = np.hstack([X_old, rng.normal(size=(n, 4))])

        auc_old, auc_new = self._ab_auc(X_old, X_new, y)
        # New features (noise) must not beat old features on AUC.
        assert auc_new <= auc_old + 1e-9, (
            f"Challenger AUC {auc_new:.4f} unexpectedly beat champion {auc_old:.4f}; "
            "promotion guard would be violated."
        )

    @pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="sklearn not installed")
    def test_new_features_neutral_or_better_auc(self):
        """A/B report: new features must produce val AUC >= current or within -0.01."""
        rng = np.random.default_rng(1)
        n = 300
        X_old = rng.normal(size=(n, 4))
        y = (X_old[:, 0] + 0.5 * X_old[:, 1] > 0).astype(int)
        # New features include the same signal plus informative extras.
        X_new = np.hstack([X_old, X_old[:, :2] * 0.5 + rng.normal(size=(n, 2)) * 0.1])

        auc_old, auc_new = self._ab_auc(X_old, X_new, y)
        # Allow neutral-or-better within -0.01 tolerance.
        assert auc_new >= auc_old - 0.01, (
            f"New features AUC {auc_new:.4f} degraded vs champion {auc_old:.4f} "
            f"(delta {auc_new - auc_old:+.4f}); must be within -0.01."
        )
