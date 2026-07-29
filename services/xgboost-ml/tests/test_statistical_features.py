"""
Tests for StatisticalFeatures: PCA, autocorrelation, and change-point detection.
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from app.feature_engine.statistical_features import StatisticalFeatures


# =============================================================================
# Helpers
# =============================================================================

MOCK_DATE = "2024-06-01"
N_STOCKS = 8
N_DAYS = 60
STOCK_CODES = [f"STOCK_{i:04d}" for i in range(N_STOCKS)]


def _make_prices(seed: int = 42) -> pd.DataFrame:
    """Build a realistic prices DataFrame with N_STOCKS × N_DAYS rows.

    Each stock has a distinct base price plus a shared market factor and
    independent noise, giving PCA something to find.
    """
    rng = np.random.RandomState(seed)
    dates = pd.date_range(end=MOCK_DATE, periods=N_DAYS, freq="B")

    rows = []
    for i, code in enumerate(STOCK_CODES):
        base = 10_000.0 * (i + 1)
        market_factor = np.cumsum(rng.normal(0, 0.001, N_DAYS))  # shared trend
        noise = rng.normal(0, 0.005, N_DAYS)
        prices = base * (1.0 + market_factor + noise)
        prices = np.maximum(prices, base * 0.5)  # floor at 50%
        for j, d in enumerate(dates):
            rows.append(
                {"stock_code": code, "trade_date": d.date(), "close_price": float(prices[j])}
            )

    return pd.DataFrame(rows)


def _make_close_prices(
    length: int = 60, pattern: str = "flat", seed: int = 42,
) -> pd.DataFrame:
    """Build a single-stock close-price DataFrame.

    *pattern* controls the price series shape:

    - ``"flat"``   – constant 50 000
    - ``"trend"``  – linearly increasing
    - ``"sine"``   – sine wave around 50 000
    - ``"step"``   – one jump half-way
    """
    if pattern == "flat":
        prices = np.full(length, 50_000.0)
    elif pattern == "trend":
        prices = 50_000.0 + np.arange(length, dtype=float) * 10.0
    elif pattern == "sine":
        t = np.linspace(0, 4 * np.pi, length)
        prices = 50_000.0 + np.sin(t) * 5_000.0
    elif pattern == "step":
        prices = np.full(length, 50_000.0, dtype=float)
        prices[length // 2 :] = 55_000.0
    else:
        raise ValueError(f"Unknown pattern: {pattern}")

    dates = pd.date_range(end=MOCK_DATE, periods=length, freq="B")
    return pd.DataFrame(
        {"trade_date": dates.date, "close_price": prices}
    )


@pytest.fixture
def stats():
    """StatisticalFeatures with a mocked storage (no real DB)."""
    mock_storage = MagicMock()
    # make _get_conn / _put_conn harmless
    mock_storage._get_conn.return_value = MagicMock()
    return StatisticalFeatures(storage=mock_storage)


# =============================================================================
# PCA features
# =============================================================================


class TestComputePCA:
    def test_returns_five_features(self, stats):
        """PCA with healthy data returns all 5 pc_N features."""
        prices_df = _make_prices()
        with patch.object(stats, "_fetch_all_market_data", return_value=prices_df):
            result = stats.compute_pca("STOCK_0000", MOCK_DATE)

        assert len(result) == 5
        for i in range(1, 6):
            key = f"pc_{i}"
            assert key in result
            assert isinstance(result[key], float)

    def test_scores_are_not_all_zero(self, stats):
        """With multiple stocks and varied prices, at least PC1 should be non-zero."""
        prices_df = _make_prices(seed=7)
        with patch.object(stats, "_fetch_all_market_data", return_value=prices_df):
            result = stats.compute_pca("STOCK_0000", MOCK_DATE)

        assert any(result[f"pc_{i}"] != 0.0 for i in range(1, 4)), (
            "Expected some non-zero PC scores with varied data"
        )

    def test_single_stock_returns_zeros(self, stats):
        """PCA with only one stock in the matrix returns all zeros."""
        single = _make_prices(seed=1).query("stock_code == 'STOCK_0000'")
        with patch.object(stats, "_fetch_all_market_data", return_value=single):
            result = stats.compute_pca("STOCK_0000", MOCK_DATE)

        assert all(result[f"pc_{i}"] == 0.0 for i in range(1, 6))

    def test_empty_data_returns_zeros(self, stats):
        """Empty DataFrame from DB returns all zeros."""
        empty = pd.DataFrame(columns=["stock_code", "trade_date", "close_price"])
        with patch.object(stats, "_fetch_all_market_data", return_value=empty):
            result = stats.compute_pca("STOCK_0000", MOCK_DATE)

        assert all(result[f"pc_{i}"] == 0.0 for i in range(1, 6))

    def test_different_stock_returns_different_scores(self, stats):
        """Two different stocks get different PC scores."""
        prices_df = _make_prices(seed=3)
        with patch.object(stats, "_fetch_all_market_data", return_value=prices_df):
            r1 = stats.compute_pca("STOCK_0000", MOCK_DATE)
            r2 = stats.compute_pca("STOCK_0001", MOCK_DATE)

        # At least one of the PCs should differ
        scores_1 = [r1[f"pc_{i}"] for i in range(1, 6)]
        scores_2 = [r2[f"pc_{i}"] for i in range(1, 6)]
        assert scores_1 != scores_2


# =============================================================================
# Autocorrelation features
# =============================================================================


class TestComputeAutocorrelation:
    def test_returns_four_features(self, stats):
        """Default lags produce ac_lag_1,5,10,20."""
        df = _make_close_prices(60, pattern="sine")
        with patch.object(stats, "_fetch_market_data", return_value=df):
            result = stats.compute_autocorrelation("STOCK_0000", MOCK_DATE)

        assert len(result) == 4
        for lag in [1, 5, 10, 20]:
            assert f"ac_lag_{lag}" in result
            assert isinstance(result[f"ac_lag_{lag}"], float)

    def test_flat_prices_return_zeros(self, stats):
        """Constant prices have no autocorrelation."""
        df = _make_close_prices(60, pattern="flat")
        with patch.object(stats, "_fetch_market_data", return_value=df):
            result = stats.compute_autocorrelation("STOCK_0000", MOCK_DATE)

        for lag in [1, 5, 10, 20]:
            assert result[f"ac_lag_{lag}"] == 0.0

    def test_insufficient_data_returns_zeros(self, stats):
        """Fewer data points than max lag returns 0 for unavailable lags."""
        df = _make_close_prices(3, pattern="trend")
        with patch.object(stats, "_fetch_market_data", return_value=df):
            result = stats.compute_autocorrelation("STOCK_0000", MOCK_DATE)

        # 3 points with linear trend → perfect positive autocorr at lag 1
        assert result["ac_lag_1"] > 0.0
        for lag in [5, 10, 20]:
            assert result[f"ac_lag_{lag}"] == 0.0  # 3 <= lag

    def test_trend_data_positive_ac(self, stats):
        """Linearly increasing prices produce positive autocorrelation."""
        df = _make_close_prices(60, pattern="trend")
        with patch.object(stats, "_fetch_market_data", return_value=df):
            result = stats.compute_autocorrelation("STOCK_0000", MOCK_DATE)

        assert result["ac_lag_1"] > 0.0

    def test_empty_data_returns_zeros(self, stats):
        """Empty DataFrame returns all zeros."""
        df = pd.DataFrame(columns=["trade_date", "close_price"])
        with patch.object(stats, "_fetch_market_data", return_value=df):
            result = stats.compute_autocorrelation("STOCK_0000", MOCK_DATE)

        assert all(result[f"ac_lag_{lag}"] == 0.0 for lag in [1, 5, 10, 20])


# =============================================================================
# Change-point features
# =============================================================================


class TestComputeChangePoint:
    def test_step_change_detected(self, stats):
        """A clear mean shift half-way should give a high cp_score."""
        rng = np.random.RandomState(42)
        length = 60
        prices = np.full(length, 50_000.0, dtype=float)
        prices[length // 2:] = 55_000.0
        prices += rng.normal(0, 100.0, length)  # small noise so std > 0
        dates = pd.date_range(end=MOCK_DATE, periods=length, freq="B")
        df = pd.DataFrame({"trade_date": dates.date, "close_price": prices})
        with patch.object(stats, "_fetch_market_data", return_value=df):
            result = stats.compute_change_point("STOCK_0000", MOCK_DATE)

        assert result["cp_score"] > 2.0
        assert isinstance(result["cp_score"], float)

    def test_flat_prices_score_zero(self, stats):
        """Constant prices produce cp_score == 0."""
        df = _make_close_prices(60, pattern="flat")
        with patch.object(stats, "_fetch_market_data", return_value=df):
            result = stats.compute_change_point("STOCK_0000", MOCK_DATE)

        assert result["cp_score"] == 0.0

    def test_small_data_single_row(self, stats):
        """Single data point can't split → score 0."""
        df = _make_close_prices(1, pattern="flat")
        with patch.object(stats, "_fetch_market_data", return_value=df):
            result = stats.compute_change_point("STOCK_0000", MOCK_DATE)

        assert result["cp_score"] == 0.0

    def test_empty_data_returns_zero(self, stats):
        """Empty DataFrame returns cp_score == 0."""
        df = pd.DataFrame(columns=["trade_date", "close_price"])
        with patch.object(stats, "_fetch_market_data", return_value=df):
            result = stats.compute_change_point("STOCK_0000", MOCK_DATE)

        assert result["cp_score"] == 0.0


# =============================================================================
# compute_all
# =============================================================================


class TestComputeAll:
    def test_returns_10_features(self, stats):
        """compute_all merges PCA (5) + autocorr (4) + cp (1) = 10."""
        prices_df = _make_prices(seed=5)
        close_df = _make_close_prices(60, pattern="sine")

        def fake_fetch_all(*args, **kwargs):
            return prices_df

        def fake_fetch_single(*args, **kwargs):
            return close_df

        with (
            patch.object(stats, "_fetch_all_market_data", side_effect=fake_fetch_all),
            patch.object(stats, "_fetch_market_data", side_effect=fake_fetch_single),
        ):
            result = stats.compute_all("STOCK_0000", MOCK_DATE)

        assert len(result) == 10
        expected_keys = (
            [f"pc_{i}" for i in range(1, 6)]
            + [f"ac_lag_{lag}" for lag in [1, 5, 10, 20]]
            + ["cp_score"]
        )
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"
            assert isinstance(result[key], float)

    def test_edge_case_all_zeros(self, stats):
        """Single-stock PCA + empty autocorr + single-row change-point → all 0."""
        single = _make_prices(seed=1).query("stock_code == 'STOCK_0000'")

        with (
            patch.object(stats, "_fetch_all_market_data", return_value=single),
            patch.object(
                stats,
                "_fetch_market_data",
                return_value=pd.DataFrame(columns=["trade_date", "close_price"]),
            ),
        ):
            result = stats.compute_all("STOCK_0000", MOCK_DATE)

        assert len(result) == 10
        assert all(result[f"pc_{i}"] == 0.0 for i in range(1, 6))
        assert all(result[f"ac_lag_{lag}"] == 0.0 for lag in [1, 5, 10, 20])
        assert result["cp_score"] == 0.0
