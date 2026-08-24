"""Kalman smoothing for denoising day-trading price series.

Extends (imports) the existing ``KalmanFeatureFilter`` in ``kalman_filter.py``
but does **not** modify it.  Adds a full Rauch–Tung–Striebel (RTS) fixed-interval
smoother so the screener can work with the *entire smoothed trend series*, not
just the scalar features the baseline filter returns.

Model
-----
    state x[k] = underlying log-return drift (momentum)
    observation z[k] = observed log-return = diff(log(close))
    transition : x[k] = x[k-1] + w[k],   w ~ N(0, Q)
    observation: z[k] = x[k]   + v[k],   v ~ N(0, R)

    R is adapted from the recent log-return variance so the filter trusts
    observations less in high-noise regimes (spread-bounce heavy periods).

Runs in O(n) — safe for a screener hot loop over thousands of symbols.
"""

from __future__ import annotations

import numpy as np

__all__ = ["KalmanSmoother"]


class KalmanSmoother:
    """Fixed-interval Kalman (RTS) smoother for trend reconstruction.

    Parameters mirror the baseline ``KalmanFeatureFilter`` so behaviour is
    consistent with the trained ``kalman_*`` features.
    """

    def __init__(self, q: float = 0.00005, r: float = 0.0006,
                 min_observations: int = 5):
        self.q = float(q)
        self.r = float(r)
        self.min_observations = int(min_observations)

    # ── public API ─────────────────────────────────────────────────────
    def smooth(self, close_prices) -> dict:
        """Smooth a price series and return denoised trend features.

        Parameters
        ----------
        close_prices : array-like
            Observed close prices (chronological, oldest first).

        Returns
        -------
        dict
            ``smoothed``  : float array — RTS-smoothed log-return trend series.
            ``observations``: float array — raw log-returns (aligned to smoothed).
            ``trend``      : float — last smoothed state (sign & strength).
            ``slope``      : float — slope of the smoothed series over the last
                ``slope_window`` points (noise-free momentum).
            ``noise_resid_std`` : float — std of (observation − smoothed) residuals.
            ``volatility_ann`` : float — annualised volatility of raw log-returns.
            ``gain``       : float — final forward Kalman gain.
            ``n_obs``      : int — number of usable observations.

        If there are too few observations, returns an all-neutral dict.
        """
        arr = self._clean_prices(close_prices)
        n = len(arr)
        if n < self.min_observations:
            return self._neutral(n)

        log_rets = self._log_returns(arr)
        m = len(log_rets)
        if m < self.min_observations:
            return self._neutral(m)

        r_adapt = self._adaptive_r(log_rets)

        # Forward filter
        x_f, p_f, kg = self._forward_filter(log_rets, r_adapt)
        # Backward RTS smoother
        x_s = self._rts_smoother(x_f, p_f)

        resid = log_rets - x_s

        slope_window = min(5, m)
        if slope_window >= 2:
            slope = float(np.polyfit(
                np.arange(slope_window), x_s[-slope_window:], 1)[0])
        else:
            slope = float(x_s[-1])

        return {
            "smoothed": x_s,
            "observations": np.asarray(log_rets, dtype=float),
            "trend": float(x_s[-1]),
            "slope": slope,
            "noise_resid_std": float(np.std(resid)) if m > 1 else 0.0,
            "volatility_ann": float(np.std(log_rets) * np.sqrt(252)) if m > 1 else 0.0,
            "gain": float(kg),
            "n_obs": m,
        }

    # ── internal helpers ───────────────────────────────────────────────
    @staticmethod
    def _clean_prices(close_prices) -> np.ndarray:
        if close_prices is None:
            return np.array([], dtype=float)
        arr = np.asarray([float(c) for c in close_prices], dtype=float)
        arr = arr[np.isfinite(arr)]
        return arr[arr > 0]

    @staticmethod
    def _log_returns(prices: np.ndarray) -> np.ndarray:
        return np.diff(np.log(prices))

    def _adaptive_r(self, log_rets: np.ndarray) -> float:
        recent = log_rets[-min(21, len(log_rets)):]
        recent_vol = float(np.std(recent)) if len(recent) > 1 else 0.0
        return max(recent_vol ** 2, self.r / 10.0)

    def _forward_filter(self, log_rets: np.ndarray, r_adapt: float):
        n = len(log_rets)
        x_f = np.zeros(n)
        p_f = np.zeros(n)
        kg = 0.0
        x_f[0] = log_rets[0]
        p_f[0] = r_adapt
        for k in range(1, n):
            x_pred = x_f[k - 1]
            p_pred = p_f[k - 1] + self.q
            kg = p_pred / (p_pred + r_adapt)
            x_f[k] = x_pred + kg * (log_rets[k] - x_pred)
            p_f[k] = (1.0 - kg) * p_pred
        return x_f, p_f, kg

    def _rts_smoother(self, x_f: np.ndarray, p_f: np.ndarray) -> np.ndarray:
        """Fixed-interval Rauch–Tung–Striebel smoother (backward pass)."""
        n = len(x_f)
        x_s = np.array(x_f, dtype=float)
        for k in range(n - 2, -1, -1):
            p_pred = p_f[k] + self.q
            if p_pred <= 0:
                continue
            c = p_f[k] / p_pred
            x_s[k] = x_f[k] + c * (x_s[k + 1] - x_f[k])
        return x_s

    def _neutral(self, n: int) -> dict:
        return {
            "smoothed": np.array([], dtype=float),
            "observations": np.array([], dtype=float),
            "trend": 0.0,
            "slope": 0.0,
            "noise_resid_std": 0.0,
            "volatility_ann": 0.0,
            "gain": 0.0,
            "n_obs": int(n),
        }
