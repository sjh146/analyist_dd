"""
GP uncertainty module (Phase 2).

Emits a per-stock *epistemic* standard deviation ``sigma`` by fitting a sklearn
``GaussianProcessRegressor`` on a LOW-dimensional feature subset (momentum /
volume / kalman features only) to predict the 5-day forward return.

Design constraints (from the plan):
- The GPR is fit OFFLINE via ``GPUncertainty.fit(X_lowdim, y_5d_return)``.
- ``GPUncertainty.predict_std(feature_vec)`` returns a float ``sigma``.
- ``effective_score = calibrated_prob - kappa * sigma`` is computed here.
- High-dimensional input (> ``MAX_FEATURES``) raises a clear error to prevent
  accidentally fitting a GPR on all 42/43 champion features.
- GPR fitting must NEVER run inside the screener hot loop; batch all-KOSDAQ
  prediction is done once via ``predict_std_batch``.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel

# Low-dimensional feature subset: momentum / volume / kalman features only.
# NOT all 42/43 champion features. Order matters — it defines the input layout.
LOW_DIM_FEATURES: List[str] = [
    "return_5d",
    "return_20d",
    "volume_ratio_5",
    "volume_ratio_20",
    "kalman_momentum_1d",
    "kalman_momentum_5d",
    "kalman_volatility",
]

# Hard guard against accidental high-dimensional GPR. The champion feature set
# has 43 features; anything above this is almost certainly a mistake.
MAX_FEATURES: int = 15


class GPUncertainty:
    """sklearn GaussianProcessRegressor wrapper emitting per-stock epistemic std.

    Usage (offline fit, then batch predict):
        gp = GPUncertainty()
        gp.fit(X_lowdim, y_5d_return)          # offline, once
        sigma = gp.predict_std(feature_vec)     # float
        sigmas = gp.predict_std_batch(X_batch)  # np.ndarray, all-KOSDAQ once
        score = gp.effective_score(calibrated_prob, sigma, kappa=0.2)
    """

    def __init__(
        self,
        feature_names: Optional[Sequence[str]] = None,
        random_state: int = 42,
        alpha: float = 1e-6,
        n_restarts_optimizer: int = 2,
    ) -> None:
        self.feature_names: List[str] = list(feature_names or LOW_DIM_FEATURES)
        if len(self.feature_names) > MAX_FEATURES:
            raise ValueError(
                f"GPUncertainty is designed for a LOW-dimensional feature subset "
                f"(<= {MAX_FEATURES} features), got {len(self.feature_names)}. "
                f"Fitting a GPR on the full feature set is unsupported — pass only "
                f"momentum/volume/kalman features."
            )
        self.n_features = len(self.feature_names)

        # Kernel: constant * RBF (signal variance + length-scale) + white noise.
        # WhiteKernel captures aleatoric noise; RBF captures epistemic structure.
        kernel = ConstantKernel(1.0, (1e-3, 1e3)) * RBF(
            length_scale=1.0, length_scale_bounds=(1e-2, 1e2)
        ) + WhiteKernel(noise_level=alpha, noise_level_bounds=(1e-6, 1e1))

        self.model = GaussianProcessRegressor(
            kernel=kernel,
            alpha=alpha,
            normalize_y=True,
            n_restarts_optimizer=n_restarts_optimizer,
            random_state=random_state,
        )
        self._is_fitted = False

    # ------------------------------------------------------------------ fit
    def fit(self, X_lowdim: np.ndarray, y_5d_return: np.ndarray) -> "GPUncertainty":
        """Fit the GPR offline to predict 5-day forward return.

        Args:
            X_lowdim: (n_samples, n_features) low-dim feature matrix. Must have
                exactly ``self.n_features`` columns.
            y_5d_return: (n_samples,) 5-day forward return targets.

        Raises:
            ValueError: if the input width does not match the configured low-dim
                feature count (guards against accidental high-dim GPR).
        """
        X = np.asarray(X_lowdim, dtype=np.float64)
        y = np.asarray(y_5d_return, dtype=np.float64).ravel()

        if X.ndim != 2:
            raise ValueError(
                f"X_lowdim must be 2D (n_samples, n_features), got shape {X.shape}"
            )
        if X.shape[1] != self.n_features:
            raise ValueError(
                f"GPUncertainty expects {self.n_features} low-dim features "
                f"(momentum/volume/kalman), got {X.shape[1]}. Refusing to fit a "
                f"high-dimensional GPR."
            )
        if len(y) != X.shape[0]:
            raise ValueError(
                f"y_5d_return length {len(y)} != X rows {X.shape[0]}"
            )

        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)

        self.model.fit(X, y)
        self._is_fitted = True
        return self

    # ------------------------------------------------------------- predict
    def predict_std(self, feature_vec: Sequence[float]) -> float:
        """Return the epistemic std ``sigma`` for a single feature vector.

        Args:
            feature_vec: length-``n_features`` low-dim feature vector.

        Returns:
            float sigma (posterior predictive std from ``return_std=True``).

        Raises:
            ValueError: if not fitted, or if the input width is wrong.
        """
        if not self._is_fitted:
            raise ValueError(
                "GPUncertainty is not fitted. Call fit(X_lowdim, y_5d_return) first."
            )
        vec = np.asarray(feature_vec, dtype=np.float64).reshape(1, -1)
        if vec.shape[1] != self.n_features:
            raise ValueError(
                f"predict_std expects {self.n_features} low-dim features, got "
                f"{vec.shape[1]}. High-dimensional input is unsupported."
            )
        vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
        _, std = self.model.predict(vec, return_std=True)
        return float(std[0])

    def predict_std_batch(self, X_batch: np.ndarray) -> np.ndarray:
        """Return epistemic std for a batch of feature vectors (all-KOSDAQ once).

        This is the batch entry point used OUTSIDE the screener hot loop.
        """
        if not self._is_fitted:
            raise ValueError(
                "GPUncertainty is not fitted. Call fit(X_lowdim, y_5d_return) first."
            )
        X = np.asarray(X_batch, dtype=np.float64)
        if X.ndim != 2 or X.shape[1] != self.n_features:
            raise ValueError(
                f"predict_std_batch expects (n, {self.n_features}) low-dim matrix, "
                f"got shape {X.shape}. High-dimensional input is unsupported."
            )
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        _, std = self.model.predict(X, return_std=True)
        return np.asarray(std, dtype=np.float64)

    # ------------------------------------------------------- effective score
    @staticmethod
    def effective_score(
        calibrated_prob: float, sigma: float, kappa: float = 0.2
    ) -> float:
        """Compute ``effective_score = calibrated_prob - kappa * sigma``.

        Args:
            calibrated_prob: calibrated up-probability in [0, 1].
            sigma: epistemic std from ``predict_std``.
            kappa: uncertainty penalty coefficient (swept over [0, 0.1, 0.2, 0.3, 0.5]).

        Returns:
            float effective score.
        """
        return float(calibrated_prob - kappa * sigma)

    # ------------------------------------------------------------- metadata
    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"GPUncertainty(n_features={self.n_features}, "
            f"fitted={self._is_fitted}, features={self.feature_names})"
        )
