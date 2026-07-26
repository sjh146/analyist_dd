"""Kalman filter for denoising financial time series features."""
import numpy as np
from typing import Dict

class KalmanFeatureFilter:
    """Kalman filter that estimates smooth momentum from noisy daily returns.

    State: underlying momentum (drift)
    Measurement: observed daily return
    Q: process noise (how fast momentum changes)
    R: measurement noise (daily return variance)
    """

    def __init__(self):
        self.Q = 0.00005
        self.R = 0.0006

    def smooth_returns(self, close_prices: np.ndarray) -> Dict:
        if close_prices is None or len(close_prices) < 5:
            return {"kalman_momentum_1d": 0.0, "kalman_momentum_5d": 0.0, "kalman_volatility": 0.0}

        prices = np.array([float(c) for c in close_prices])
        log_rets = np.diff(np.log(prices))

        if len(log_rets) < 3:
            return {"kalman_momentum_1d": 0.0, "kalman_momentum_5d": 0.0, "kalman_volatility": 0.0}

        recent_vol = np.std(log_rets[-min(21, len(log_rets)):])
        R_adapt = max(recent_vol**2, self.R / 10)

        n = len(log_rets)
        x_est = np.zeros(n)
        p_est = np.zeros(n)

        x_est[0] = log_rets[0]
        p_est[0] = R_adapt

        for k in range(1, n):
            x_pred = x_est[k-1]
            p_pred = p_est[k-1] + self.Q

            kg = p_pred / (p_pred + R_adapt)
            x_est[k] = x_pred + kg * (log_rets[k] - x_pred)
            p_est[k] = (1 - kg) * p_pred

        return {
            "kalman_momentum_1d": float(x_est[-1]),
            "kalman_momentum_5d": float(np.mean(x_est[-5:])) if n >= 5 else float(np.mean(x_est)),
            "kalman_volatility": float(np.std(log_rets[-min(20, len(log_rets)):]) * np.sqrt(252)),
            "kalman_gain": float(kg),
        }
