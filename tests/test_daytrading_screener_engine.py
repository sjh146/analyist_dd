"""Day-trading screener DB-free unit tests.

Covers:
 1. Kalman (RTS) smoothing denoising — synthetic trend + noise restoration.
 2. Scoring formula / ranking (bounds, monotonicity, clamping, order).
 3. FixtureProvider-driven full pipeline (no DB required).
"""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "xgboost-ml"))

from day_trading_engine import (FixtureProvider, rank_candidates, run_screener,
                                score_candidates)
from day_trading_engine.providers import StockInfo
from app.feature_engine.kalman_smoother import KalmanSmoother


# ── fixtures / helpers ─────────────────────────────────────────────────
def _ohlcv_frame(code, closes, vols, values=None, start=date(2026, 7, 1)):
    n = len(closes)
    if values is None:
        values = [1e9] * n
    return pd.DataFrame({
        "stock_code": [code] * n,
        "trade_date": [date.fromordinal(start.toordinal() + i) for i in range(n)],
        "open_price": [float(c) for c in closes],
        "high_price": [float(c) * 1.02 for c in closes],
        "low_price": [float(c) * 0.98 for c in closes],
        "close_price": [float(c) for c in closes],
        "volume": [float(v) for v in vols],
        "trading_value": [float(v) for v in values],
    })


def _up_trend(n=60, drift=0.004, noise=0.008, seed=0):
    """Deterministic upward trend + white noise → noisy price series (levels)."""
    rng = np.random.default_rng(seed)
    rets = np.full(n, drift) + rng.normal(0, noise, n)
    levels = 100.0 * np.exp(np.cumsum(rets))
    return levels


# ── 1. Kalman denoising verification ───────────────────────────────────
class TestKalmanDenoising:
    def test_smoothed_recovers_true_trend(self):
        levels = _up_trend(n=80, drift=0.005, noise=0.01, seed=7)
        ks = KalmanSmoother()
        out = ks.smooth(levels)
        obs = out["observations"]
        sm = out["smoothed"]
        # True trend drift is positive → smoothed & trend must be positive.
        assert out["trend"] > 0
        assert out["slope"] > 0
        # Residual noise (obs − smoothed) should be well below injected noise.
        resid_std = out["noise_resid_std"]
        assert resid_std < 0.01
        # Smoothed series should be far smoother than raw observations.
        assert np.std(np.diff(sm)) < np.std(np.diff(obs))

    def test_high_noise_reduces_trust(self):
        # Inject very large observation noise relative to trend.
        rng = np.random.default_rng(3)
        n = 60
        levels = 100.0 * np.exp(np.cumsum(
            np.full(n, 0.001) + rng.normal(0, 0.05, n)))
        ks = KalmanSmoother()
        out = ks.smooth(levels)
        assert out["noise_resid_std"] < 0.05  # smoother pulls out most noise

    def test_insufficient_observations_neutral(self):
        ks = KalmanSmoother(min_observations=5)
        out = ks.smooth([100.0, 101.0, 102.0])  # only 2 log-returns
        assert out["trend"] == 0.0
        assert out["slope"] == 0.0
        assert out["n_obs"] == 3

    def test_slope_sign_up_vs_down(self):
        # Geometric drift: constant positive/negative log-return → kalman trend sign follows.
        n = 60
        up = 100.0 * np.exp(np.cumsum(np.full(n, 0.006) + np.random.default_rng(1).normal(0, 0.002, n)))
        down = 100.0 * np.exp(np.cumsum(np.full(n, -0.006) + np.random.default_rng(2).normal(0, 0.002, n)))
        ks = KalmanSmoother()
        up_out = ks.smooth(up)
        down_out = ks.smooth(down)
        assert up_out["trend"] > 0
        assert down_out["trend"] < 0


# ── 2. Scoring formula / ranking ───────────────────────────────────────
class TestScoring:
    def _kf_frame(self, **rows):
        # Build a feature frame mimicking compute_kalman_features output.
        default = {
            "stock_code": ["A"],
            "signal_date": ["2026-07-25"],
            "close_price": [5000.0],
            "volume": [1.2e6],
            "trading_value": [1e9],
            "kalman_trend": [0.005],
            "kalman_slope": [0.0002],
            "noise_resid_std": [0.001],
            "volatility_ann": [0.5],
            "volume_surge": [2.0],
            "day_change": [0.03],
        }
        default.update(rows)
        return pd.DataFrame(default)

    def test_score_within_bounds_with_probs(self):
        df = self._kf_frame(stock_code=["A"])
        probs = pd.Series({"A": 0.8})
        out = score_candidates(df, probs=probs)
        assert 0 <= out.iloc[0]["score"] <= 100
        assert out.iloc[0]["model_prob"] == pytest.approx(0.8)

    def test_clean_slope_ranks_higher(self):
        base = self._kf_frame()
        clean = base.copy()
        clean["kalman_slope"] = [-0.0001]
        noisy = base.copy()
        noisy["kalman_slope"] = [0.0002]
        # give same noise std so slope dominates
        noisy["noise_resid_std"] = [0.001]
        base["noise_resid_std"] = [0.001]
        s_clean = score_candidates(pd.concat([base, clean])).loc[0, "score"]
        s_noisy = score_candidates(pd.concat([base, noisy])).loc[1, "score"]
        assert s_noisy < s_clean

    def test_volume_surge_monotonicity(self):
        low = score_candidates(self._kf_frame(volume_surge=[1.0])).iloc[0]["score"]
        high = score_candidates(self._kf_frame(volume_surge=[4.0])).iloc[0]["score"]
        assert high > low

    def test_model_prob_monotonicity(self):
        base = self._kf_frame(stock_code=["A"])
        p_low = score_candidates(base.copy(), probs=pd.Series({"A": 0.55})).iloc[0]
        p_high = score_candidates(base.copy(), probs=pd.Series({"A": 0.85})).iloc[0]
        assert p_high["score"] > p_low["score"]

    def test_no_model_flag_in_reason(self):
        df = self._kf_frame(stock_code=["A"])
        out = score_candidates(df)  # probs=None
        assert "모델미가용" in out.iloc[0]["reason"]
        assert out.iloc[0]["model_prob"] is None

    def test_nan_volume_surge_scores_zero_vol_component(self):
        df = self._kf_frame(stock_code=["A"], volume_surge=[np.nan])
        out = score_candidates(df)
        assert out.iloc[0]["volume_surge"] != out.iloc[0]["volume_surge"]  # is NaN
        assert np.isfinite(out.iloc[0]["score"])


class TestRanking:
    def test_sorts_desc_by_score_and_assigns_rank(self):
        df = pd.DataFrame({
            "stock_code": ["a", "b", "c"],
            "score": [10.0, 90.0, 50.0],
            "signal_date": ["x", "x", "x"], "close_price": [1.0, 1.0, 1.0],
            "volume": [1.0, 1.0, 1.0], "trading_value": [1e9, 1e9, 1e9],
        })
        out = rank_candidates(df, top_n=2)
        assert list(out["stock_code"]) == ["b", "c"]
        assert list(out["rank"]) == [1, 2]

    def test_empty_rank_returns_empty(self):
        df = pd.DataFrame(columns=["stock_code", "score"])
        out = rank_candidates(df)
        assert out.empty


# ── 3. FixtureProvider full pipeline (DB-free) ─────────────────────────
class TestPipelineFixture:
    def _multi_stock_frame(self):
        # A: strong clean uptrend, liquid; B: low price (<1000) → filtered.
        a = _ohlcv_frame("000001", np.round(np.linspace(5000, 9000, 25), 2),
                         np.linspace(1e6, 1.8e6, 25))
        b = _ohlcv_frame("000002", np.linspace(200, 300, 25), np.full(25, 2e6))
        return pd.concat([a, b], ignore_index=True)

    def test_full_pipeline_ranks_and_filters(self):
        df = self._multi_stock_frame()
        prov = FixtureProvider(df, signal_date="2026-07-25")
        out = run_screener(prov, top_n=5, lookback=20, min_history=10,
                           min_price=1000)
        # Low-price stock B filtered; A kept.
        assert set(out["stock_code"]) == {"000001"}
        assert out.iloc[0]["rank"] == 1

    def test_empty_result_when_all_filtered(self):
        low = _ohlcv_frame("000099", np.linspace(100, 300, 25), np.full(25, 2e6))
        prov = FixtureProvider(low, signal_date="2026-07-25")
        out = run_screener(prov, top_n=5, lookback=20, min_history=10,
                           min_price=1000)
        assert out.empty

    def test_explicit_meta_and_sector_mapping(self):
        df = self._multi_stock_frame()
        meta = [StockInfo("000001", "예시주A", "테크"),
                StockInfo("000002", "예시주B", "금융")]
        prov = FixtureProvider(df, meta=meta, signal_date="2026-07-25")
        out = run_screener(prov, top_n=5, lookback=20, min_history=10,
                           min_price=1000)
        assert out.iloc[0]["stock_name"] == "예시주A"
        assert out.iloc[0]["sector"] == "테크"

    def test_fake_predictor_drives_ranking(self):
        n = 25
        levels_a = 5000.0 * np.exp(np.arange(n) * 0.02)   # strong geometric uptrend
        levels_b = 5000.0 * np.exp(np.arange(n) * 0.001)  # flat
        high_a = _ohlcv_frame("A", np.round(levels_a, 2), np.linspace(1e6, 1.8e6, n))
        high_b = _ohlcv_frame("B", np.round(levels_b, 2), np.linspace(1.2e6, 1.4e6, n))
        prov = FixtureProvider(pd.concat([high_a, high_b], ignore_index=True),
                               signal_date="2026-07-25")

        class FakePredictor:
            available = True
            def predict(self, df):
                return pd.Series({"A": 0.9, "B": 0.51}, dtype=float)

        out = run_screener(prov, top_n=5, lookback=20, min_history=10,
                           min_price=1000, predictor=FakePredictor())
        assert out.iloc[0]["stock_code"] == "A"
        assert out.iloc[0]["model_prob"] == pytest.approx(0.9)
