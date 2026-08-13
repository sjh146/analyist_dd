"""Tests for the offline effective_score fitting script + artifact loader.

Runs with light MCMC (warmup=5, samples=8) so the suite stays fast; the real
fit is a container-side batch job (``app/training/fit_effective_score.py``).
"""

import json
import os
import pickle

import numpy as np
import pandas as pd
import pytest

from app.scoring.effective_score import (
    DEFAULT_EFFECTIVE_SCORE_DIR,
    EffectiveScore,
    load_effective_scorer,
    resolve_effective_score_dir,
)
from app.training.fit_effective_score import (
    expected_calibration_error,
    fit_effective_score_from_df,
)
from app.uncertainty.gp_uncertainty import LOW_DIM_FEATURES

N_STOCKS = 6
N_DATES = 30


def _synthetic_df() -> pd.DataFrame:
    rng = np.random.RandomState(7)
    rows = []
    dates = pd.date_range("2026-01-01", periods=N_DATES, freq="B").strftime("%Y-%m-%d")
    for s in range(N_STOCKS):
        code = f"{s:06d}"
        price = 100.0 + np.cumsum(rng.normal(0, 1.5, N_DATES))
        price = np.maximum(price, 10.0)
        for i, d in enumerate(dates):
            r5 = price[i] / price[max(0, i - 5)] - 1 if i >= 5 else 0.0
            r20 = price[i] / price[max(0, i - 20)] - 1 if i >= 20 else 0.0
            rows.append({
                "date": d,
                "stock_code": code,
                "price": price[i],
                "return_5d": r5,
                "return_20d": r20,
                "volume_ratio_5": 0.9 + rng.rand() * 0.3,
                "volume_ratio_20": 0.95 + rng.rand() * 0.2,
                "kalman_momentum_1d": rng.normal(0, 0.005),
                "kalman_momentum_5d": rng.normal(0, 0.004),
                "kalman_volatility": 0.2 + rng.rand() * 0.1,
            })
    return pd.DataFrame(rows)


def _prob_fn(X: np.ndarray) -> np.ndarray:
    """Deterministic, weakly signal-bearing pseudo-probability."""
    p = 0.5 + 0.3 * np.tanh(X[:, 0] * 3.0)
    return np.clip(p, 0.05, 0.95)


@pytest.fixture()
def artifacts_dir(tmp_path):
    df = _synthetic_df()
    meta = fit_effective_score_from_df(
        df=df,
        prob_fn=_prob_fn,
        champion_feature_names=["return_5d", "volume_ratio_5"],
        out_dir=str(tmp_path),
        num_warmup=5,
        num_samples=8,
        bayes_ref_close=df.groupby("stock_code")["price"].first().tolist(),
        bayes_ref_stock="ref",
        gp_max_rows=500,
        seed=0,
    )
    return tmp_path, meta


def test_fit_persists_all_artifacts(artifacts_dir):
    d, meta = artifacts_dir
    for name in ("calibrator.pkl", "gp.pkl", "bayes_factors.pkl", "meta.json"):
        assert os.path.exists(os.path.join(d, name)), f"missing {name}"
    assert 0.0 <= meta["ece_pre"] <= 1.0
    assert 0.0 <= meta["ece_post"] <= 1.0
    assert meta["gp_features"] == LOW_DIM_FEATURES
    assert meta["bayes_posterior_cached"] is True
    assert "kappa" in meta and meta["kappa"] == 0.3


def test_fit_improves_or_keeps_calibration(artifacts_dir):
    _, meta = artifacts_dir
    # With light MCMC on tiny synthetic data the mapping may be neutral, but
    # it must never be worse than a no-op by a large margin, and the posterior
    # uncertainty must be recorded.
    assert meta["ece_post"] <= meta["ece_pre"] + 0.15
    assert meta["calibrator_rhat"], "rhat diagnostics must be recorded"


def test_gp_std_finite(artifacts_dir):
    d, _ = artifacts_dir
    with open(os.path.join(d, "gp.pkl"), "rb") as f:
        gp = pickle.load(f)
    vec = np.array([0.01, 0.02, 1.0, 1.0, 0.001, 0.001, 0.2])
    sigma = gp.predict_std(vec)
    assert np.isfinite(sigma) and sigma >= 0.0
    batch = np.tile(vec, (5, 1))
    sigmas = gp.predict_std_batch(batch)
    assert sigmas.shape == (5,) and np.all(np.isfinite(sigmas))


def test_bayes_factors_compute_uses_cached_posterior(artifacts_dir):
    d, _ = artifacts_dir
    with open(os.path.join(d, "bayes_factors.pkl"), "rb") as f:
        bf = pickle.load(f)
    assert bf._posterior is not None
    feats = bf.compute(np.linspace(100, 130, 30))
    for name in bf.FEATURE_NAMES:
        assert name in feats
        assert np.isfinite(feats[name])
    # No MCMC on compute: posterior must remain identical after a compute call.
    post_before = {k: np.asarray(v).copy() for k, v in bf._posterior.items()}
    bf.compute(np.linspace(90, 120, 30))
    post_after = {k: np.asarray(v) for k, v in bf._posterior.items()}
    for k in post_before:
        np.testing.assert_array_equal(post_before[k], post_after[k])


def test_load_effective_scorer_roundtrip(artifacts_dir, monkeypatch):
    d, meta = artifacts_dir
    scorer, bayes_factors, loaded_meta = load_effective_scorer(str(d))
    assert isinstance(scorer, EffectiveScore)
    assert scorer.calibrator is not None
    assert scorer.gp is not None
    assert bayes_factors is not None
    assert loaded_meta is not None and loaded_meta["kappa"] == 0.3

    # score() with the fitted components (low-dim vec -> sigma path).
    out = scorer.score(0.7, feature_vec=np.array([0.01, 0.02, 1.0, 1.0, 0.001, 0.001, 0.2]))
    assert 0.0 <= out["effective_score"] <= 1.0
    assert 0.0 <= out["calibrated_probability"] <= 1.0
    assert out["epistemic_std"] is not None and out["epistemic_std"] >= 0.0

    # resolve_effective_score_dir: explicit > env > default.
    assert resolve_effective_score_dir("/tmp/x") == "/tmp/x"
    monkeypatch.setenv("EFFECTIVE_SCORE_DIR", "/env/dir")
    assert resolve_effective_score_dir() == "/env/dir"
    monkeypatch.delenv("EFFECTIVE_SCORE_DIR")
    assert resolve_effective_score_dir() == DEFAULT_EFFECTIVE_SCORE_DIR


def test_load_effective_scorer_fallback_when_missing(tmp_path):
    scorer, bayes_factors, meta = load_effective_scorer(str(tmp_path))
    assert isinstance(scorer, EffectiveScore)
    assert scorer.calibrator is None and scorer.gp is None
    assert bayes_factors is None and meta is None
    # Fallback: raw prob passes through.
    out = scorer.score(0.6)
    assert out["effective_score"] == pytest.approx(0.6)
    assert out["epistemic_std"] is None


def test_ece_helpers():
    y = np.array([0, 1, 1, 0, 1, 1, 0, 0, 1, 0])
    p = np.array([0.1, 0.9, 0.8, 0.2, 0.7, 0.6, 0.3, 0.4, 0.5, 0.5])
    assert 0.0 <= expected_calibration_error(y, p) <= 1.0
    # Perfectly calibrated dummy scores -> ECE near 0.
    assert expected_calibration_error(y, y.astype(float)) < 1e-9
