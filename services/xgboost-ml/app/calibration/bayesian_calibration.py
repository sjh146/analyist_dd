"""Bayesian probability calibration using NumPyro.

Models the calibrated probability as a Bernoulli with a Beta prior and fits the
posterior OFFLINE with NUTS. The posterior mean is recorded as
``calibrated_probability`` and the posterior std as ``calibration_uncertainty``.

This module is OFFLINE-ONLY: ``fit`` runs MCMC and must never be called from the
screener hot loop. The hot loop only calls ``calibrate`` (a cheap posterior
lookup).
"""

import logging
from typing import Dict, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

try:
    import jax
    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist
    from numpyro import diagnostics
    from numpyro.infer import MCMC, NUTS

    _NUMPYRO_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when deps missing
    _NUMPYRO_AVAILABLE = False


def _validate_probs(probs) -> np.ndarray:
    """Validate a probability array and return it as a 1-D float64 ndarray."""
    arr = np.asarray(probs, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        raise ValueError("probs must be a non-empty array of probabilities")
    if np.any(~np.isfinite(arr)):
        raise ValueError("probs contains NaN or infinite values")
    if np.any(arr < 0.0) or np.any(arr > 1.0):
        raise ValueError("probs must lie within [0, 1]")
    return arr


def _logit(x):
    return jnp.log(x / (1.0 - x))


def _sigmoid(x):
    return 1.0 / (1.0 + jnp.exp(-x))


def _calibration_model(probs, y=None):
    """NumPyro Bayesian Platt calibration model.

    ``logit(p_cal) = a + b * logit(probs)`` where ``a`` is derived from a Beta
    prior on the base-rate probability ``p0`` and ``b`` is a slope centered at
    the identity (``b=1`` means no slope adjustment). ``y ~ Bernoulli(p_cal)``.
    """
    p0 = numpyro.sample("p0", dist.Beta(2.0, 2.0))
    a = _logit(p0)
    b = numpyro.sample("b", dist.Normal(1.0, 0.5))
    with numpyro.plate("N", probs.shape[0]):
        p_cal = _sigmoid(a + b * _logit(probs))
        p_cal = numpyro.deterministic("p_cal", p_cal)
        numpyro.sample("obs", dist.Bernoulli(probs=p_cal), obs=y)


class BayesianCalibrator:
    """Bayesian (NumPyro NUTS) calibration of raw probabilities.

    Example
    -------
    >>> cal = BayesianCalibrator(num_warmup=200, num_samples=300)
    >>> cal.fit(probs_train, y_train)
    >>> cal.calibrate(0.7)
    {'calibrated_probability': 0.63, 'calibration_uncertainty': 0.04}
    """

    def __init__(
        self,
        num_warmup: int = 500,
        num_samples: int = 1000,
        num_chains: int = 2,
        rhat_threshold: float = 1.1,
        seed: int = 0,
    ):
        if not _NUMPYRO_AVAILABLE:
            raise ImportError(
                "numpyro is required for BayesianCalibrator. "
                "Install with: pip install 'numpyro[cpu]'"
            )
        self.num_warmup = num_warmup
        self.num_samples = num_samples
        self.num_chains = num_chains
        self.rhat_threshold = rhat_threshold
        self.seed = seed
        self._alpha_samples: Optional[np.ndarray] = None
        self._beta_samples: Optional[np.ndarray] = None
        self._fitted = False
        self.rhat: Dict[str, float] = {}
        self._converged = True

    def fit(self, probs, y) -> "BayesianCalibrator":
        """Fit the posterior offline with NUTS.

        Parameters
        ----------
        probs : array-like of shape (n_samples,)
            Raw ensemble probabilities used as the calibration input.
        y : array-like of shape (n_samples,)
            Binary ground-truth labels (0/1).
        """
        if not _NUMPYRO_AVAILABLE:
            raise ImportError(
                "numpyro is required for BayesianCalibrator. "
                "Install with: pip install 'numpyro[cpu]'"
            )
        p = _validate_probs(probs)
        y_arr = np.asarray(y).reshape(-1)
        if p.shape[0] != y_arr.shape[0]:
            raise ValueError("probs and y must have the same length")
        if not np.all(np.isin(y_arr, [0, 1])):
            raise ValueError("y must contain only binary labels 0/1")

        probs_jax = jnp.asarray(p, dtype=jnp.float32)
        y_jax = jnp.asarray(y_arr, dtype=jnp.float32)

        mcmc = MCMC(
            NUTS(_calibration_model),
            num_warmup=self.num_warmup,
            num_samples=self.num_samples,
            num_chains=self.num_chains,
        )
        mcmc.run(jax.random.key(self.seed), probs_jax, y=y_jax)

        samples = mcmc.get_samples(group_by_chain=True)
        p0_samples = np.asarray(samples["p0"])
        self._a_samples = np.log(p0_samples / (1.0 - p0_samples))
        self._b_samples = np.asarray(samples["b"])

        # rhat diagnostics (per-parameter, chain-aware).
        summary = diagnostics.summary(samples)
        self.rhat = {
            name: float(summary[name]["r_hat"]) for name in ("p0", "b")
        }
        worst = max(self.rhat.values())
        if worst > self.rhat_threshold:
            self._converged = False
            logger.warning(
                "BayesianCalibrator NUTS did not converge: max rhat=%.3f "
                "exceeds threshold %.2f",
                worst,
                self.rhat_threshold,
            )

        self._fitted = True
        return self

    def calibrate(self, probs) -> Dict[str, Union[float, np.ndarray]]:
        """Return calibrated probability and uncertainty for raw probability.

        Accepts a scalar or an array. Returns a dict with keys
        ``calibrated_probability`` and ``calibration_uncertainty`` holding
        floats for scalar input and ndarrays for array input.
        """
        if not self._fitted:
            raise RuntimeError(
                "BayesianCalibrator must be fit before calibrate()"
            )
        p = _validate_probs(probs)

        # Posterior samples (all chains flattened).
        a = self._a_samples.reshape(-1)
        b = self._b_samples.reshape(-1)

        logit_p = np.log(p / (1.0 - p))
        # Posterior predictive of the calibrated probability per sample.
        p_cal = 1.0 / (1.0 + np.exp(-(a[:, None] + b[:, None] * logit_p[None, :])))

        # Point estimate: posterior mean of parameters plugged into the model.
        a_mean = float(np.mean(a))
        b_mean = float(np.mean(b))
        mean = 1.0 / (1.0 + np.exp(-(a_mean + b_mean * logit_p)))
        std = np.std(p_cal, axis=0)

        if p.size == 1:
            return {
                "calibrated_probability": float(mean[0]),
                "calibration_uncertainty": float(std[0]),
            }
        return {
            "calibrated_probability": mean,
            "calibration_uncertainty": std,
        }

    @property
    def converged(self) -> bool:
        """Whether the last NUTS fit met the rhat convergence threshold."""
        return self._converged
