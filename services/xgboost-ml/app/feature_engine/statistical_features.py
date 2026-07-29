"""
Statistical Features
Extracts PCA, autocorrelation, and change-point features from market data.
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional

from sklearn.decomposition import PCA
from app.storage.postgres_storage import PostgresStorage

logger = logging.getLogger(__name__)


class StatisticalFeatures:
    """Features derived from statistical analysis of price and return data.

    Provides PCA factors (cross-sectional return patterns), autocorrelation
    (serial dependence in prices), and change-point detection (structural breaks).
    """

    def __init__(self, storage: Optional[PostgresStorage] = None):
        self.storage = storage or PostgresStorage()

    # ------------------------------------------------------------------
    # Internal data fetching helpers
    # ------------------------------------------------------------------

    def _fetch_market_data(
        self, stock_code: str, date: str, window: int = 60
    ) -> pd.DataFrame:
        """Fetch close_price for a single stock up to the given date."""
        conn = self.storage._get_conn()
        if not conn:
            return pd.DataFrame()
        try:
            query = """
                SELECT trade_date, close_price
                FROM market_data
                WHERE stock_code = %s AND trade_date <= %s
                ORDER BY trade_date DESC
                LIMIT %s
            """
            df = pd.read_sql(query, conn, params=(stock_code, date, window))
            if not df.empty:
                df = df.sort_values("trade_date").reset_index(drop=True)
            return df
        except Exception as e:
            logger.debug("_fetch_market_data failed for %s: %s", stock_code, e)
            return pd.DataFrame()
        finally:
            self.storage._put_conn(conn)

    def _fetch_all_market_data(self, date: str, window: int = 60) -> pd.DataFrame:
        """Fetch close prices for all stocks over the given window."""
        conn = self.storage._get_conn()
        if not conn:
            return pd.DataFrame()
        try:
            start = (
                pd.Timestamp(date) - pd.Timedelta(days=window * 2 + 10)
            ).strftime("%Y-%m-%d")
            query = """
                SELECT stock_code, trade_date, close_price
                FROM market_data
                WHERE trade_date <= %s AND trade_date >= %s
                ORDER BY stock_code, trade_date
            """
            df = pd.read_sql(query, conn, params=(date, start))
            return df
        except Exception as e:
            logger.debug("_fetch_all_market_data failed: %s", e)
            return pd.DataFrame()
        finally:
            self.storage._put_conn(conn)

    # ------------------------------------------------------------------
    # Public feature methods
    # ------------------------------------------------------------------

    def compute_pca(
        self,
        stock_code: str,
        date: str,
        n_components: int = 5,
        window: int = 60,
    ) -> Dict:
        """Compute PCA features from cross-sectional return patterns.

        Fetches *window* days of close prices for all stocks, computes daily
        returns, fits PCA, and returns the given stock's scores on the first
        *n_components* principal components.

        Returns
        -------
        dict  ``{pc_1: float, pc_2: float, ..., pc_N: float}``
        """
        result = {f"pc_{i+1}": 0.0 for i in range(n_components)}

        df = self._fetch_all_market_data(date, window)
        if df.empty:
            return result

        pivot = df.pivot_table(
            index="stock_code", columns="trade_date", values="close_price"
        )

        min_obs = max(2, int(window * 0.8))
        pivot = pivot.dropna(thresh=min_obs, axis=0)

        if pivot.shape[0] < 2:
            return result

        dates_sorted = sorted(pivot.columns, reverse=True)
        recent_dates = sorted(dates_sorted[:window])
        pivot = pivot[recent_dates]

        prices = pivot.values.astype(np.float64)
        returns = prices[:, 1:] / prices[:, :-1] - 1
        returns = np.nan_to_num(returns, nan=0.0, posinf=0.0, neginf=0.0)

        n_eff = min(n_components, min(returns.shape))
        if n_eff < 1:
            return result

        pca = PCA(n_components=n_eff)
        scores = pca.fit_transform(returns)

        if stock_code not in pivot.index:
            return result
        stock_idx = pivot.index.get_loc(stock_code)

        for k in range(n_components):
            if k < n_eff:
                result[f"pc_{k+1}"] = float(scores[stock_idx, k])

        return result

    def compute_autocorrelation(
        self,
        stock_code: str,
        date: str,
        lags: Optional[List[int]] = None,
    ) -> Dict:
        """Compute autocorrelation features at specified lags.

        Uses ``pandas.Series.autocorr()`` on 60 days of close prices.

        Returns
        -------
        dict  ``{ac_lag_<lag>: float, ...}``
        """
        if lags is None:
            lags = [1, 5, 10, 20]

        result = {f"ac_lag_{lag}": 0.0 for lag in lags}

        df = self._fetch_market_data(stock_code, date, window=60)
        if df.empty or "close_price" not in df.columns:
            return result

        close = df["close_price"].values.astype(np.float64)
        if len(close) < 2:
            return result

        series = pd.Series(close)
        for lag in lags:
            if len(close) > lag:
                ac = series.autocorr(lag=lag)
                if not np.isnan(ac):
                    result[f"ac_lag_{lag}"] = float(ac)

        return result

    def compute_change_point(
        self, stock_code: str, date: str, window: int = 60
    ) -> Dict:
        """Detect structural changes using a two-sample mean comparison.

        Splits the most recent *window* observations in half and computes
        ``|mean₁ - mean₂| / pooled_std``.

        Returns
        -------
        dict  ``{cp_score: float}``
        """
        result = {"cp_score": 0.0}

        df = self._fetch_market_data(stock_code, date, window=window)
        if df.empty or "close_price" not in df.columns:
            return result

        close = df["close_price"].values.astype(np.float64)
        n = len(close)
        if n < 2:
            return result

        if n > window:
            close = close[-window:]
            n = window

        half = n // 2
        if half < 1:
            return result

        first_half = close[:half]
        second_half = close[half:]

        mean1 = float(np.mean(first_half))
        mean2 = float(np.mean(second_half))
        std1 = float(np.std(first_half, ddof=1))
        std2 = float(np.std(second_half, ddof=1))

        n1 = len(first_half)
        n2 = len(second_half)
        pooled_var = ((n1 - 1) * std1 ** 2 + (n2 - 1) * std2 ** 2) / (n1 + n2 - 2)

        if pooled_var > 0:
            result["cp_score"] = float(abs(mean1 - mean2) / np.sqrt(pooled_var))

        return result

    def compute_all(self, stock_code: str, date: str) -> Dict:
        """Compute all statistical features (10 features).

        Calls ``compute_pca``, ``compute_autocorrelation``, and
        ``compute_change_point``, merging their results.
        """
        features = {}
        features.update(self.compute_pca(stock_code, date))
        features.update(self.compute_autocorrelation(stock_code, date))
        features.update(self.compute_change_point(stock_code, date))
        return features
