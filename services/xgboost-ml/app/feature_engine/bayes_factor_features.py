"""Bayesian momentum/factor features via a NumPyro state-space model.

This module provides a parallel, Bayesian alternative to the Kalman filter
features. It fits a local-linear-trend (momentum) state-space model offline
with NumPyro NUTS and returns BOTH the posterior mean and the posterior
standard deviation (uncertainty) for each feature.

The Kalman filter in ``kalman_filter.py`` fixes Q and discards the posterior
variance. This Bayesian extension preserves the full posterior distribution so
downstream models can consume uncertainty information (``bayes_gain_uncertainty``).

Heavy inference (NUTS MCMC) is run OFFLINE via ``fit(close_prices)``, which fits
the model once and caches the posterior. ``compute(close_prices)`` performs only
a cheap forward pass over the cached posterior — it NEVER runs MCMC. If no
posterior has been fit yet, ``compute()`` falls back to 0.0 defaults with a
warning. This keeps MCMC out of the screener hot loop.
"""
import logging
from typing import Dict

import numpy as np

logger = logging.getLogger(__name__)

try:
    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist
    from numpyro.infer import MCMC, NUTS
    from jax import random

    _NUMPYRO_AVAILABLE = True
except Exception:  # pragma: no cover - import guard for environments without numpyro
    _NUMPYRO_AVAILABLE = False
    logger.warning("numpyro/jax not available; BayesFactorFeatures will fall back to defaults")


class BayesFactorFeatures:
    """Bayesian momentum/factor features from a NumPyro state-space model.

    State: latent momentum (drift) that evolves as a random walk.
    Measurement: observed daily log-return.
    Priors: weakly informative Normal/HalfNormal so the model adapts to the
    observed return distribution instead of relying on fixed Q/R.

    ``compute(close_prices)`` returns a dict with:
        - ``bayes_momentum_1d``: posterior mean of the latest momentum state
        - ``bayes_momentum_5d``: posterior mean of the 5-day average momentum
        - ``bayes_volatility``: posterior mean of annualized return volatility
        - ``bayes_gain_uncertainty``: posterior std of the latest momentum state

    ``compute`` uses the posterior cached by ``fit`` (offline). If no posterior
    has been fit, it returns 0.0 defaults with a warning — it never runs MCMC.
    """

    # Feature names produced by this module (used for feature_count accounting).
    FEATURE_NAMES = [
        "bayes_momentum_1d",
        "bayes_momentum_5d",
        "bayes_volatility",
        "bayes_gain_uncertainty",
    ]

    def __init__(self, num_warmup: int = 200, num_samples: int = 200, seed: int = 0):
        self.num_warmup = num_warmup
        self.num_samples = num_samples
        self.seed = seed
        # Cached posterior from the offline ``fit`` call. None until fit runs.
        self._posterior = None

    # ------------------------------------------------------------------
    # NumPyro model
    # ------------------------------------------------------------------
    @staticmethod
    def _build_momentum_prior(init_momentum, sigma_state, n):
        """Construct a first-order Markov (random-walk) prior over momentum states.

        Returns a length-n vector where momentum[0] = init_momentum and
        momentum[t] = momentum[t-1] + Normal(0, sigma_state).
        """
        increments = numpyro.sample(
            "momentum_increments",
            dist.Normal(jnp.zeros(n - 1), sigma_state),
        )
        momentum = jnp.concatenate(
            [jnp.array([init_momentum]), init_momentum + jnp.cumsum(increments)]
        )
        return numpyro.deterministic("momentum", momentum)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def compute(self, close_prices) -> Dict:
        """Compute Bayesian momentum/factor features from close prices.

        Uses the posterior cached by ``fit`` (offline). Does NOT run MCMC. If no
        posterior has been fit yet, falls back to 0.0 defaults with a warning.

        Args:
            close_prices: iterable of close prices (list/array/Series).

        Returns:
            Dict with keys ``bayes_momentum_1d``, ``bayes_momentum_5d``,
            ``bayes_volatility``, ``bayes_gain_uncertainty``. Falls back to
            0.0 defaults when data is insufficient, numpyro is unavailable, or
            no posterior has been fit.
        """
        defaults = {name: 0.0 for name in self.FEATURE_NAMES}

        if close_prices is None or len(close_prices) < 5:
            return dict(defaults)

        prices = np.array([float(c) for c in close_prices])
        log_rets = np.diff(np.log(prices))

        if len(log_rets) < 3:
            return dict(defaults)

        if self._posterior is None:
            # 실측 문제(2026-09-24): feature_pipeline 은 BayesFactorFeatures() 를
            # 만들고 곧바로 compute() 를 부른다 — fit() 을 부르는 코드는 오프라인
            # fit_effective_score.py 뿐이고 그 산출물(bayes_factors.pkl)은 학습
            # 피처 빌드 경로에서 로드되지 않는다. 그래서 4개 베이즈 피처가 전부
            # 0.0 이었다(로그: "compute called before fit() ... returning default").
            # MCMC 를 핫 루프에 넣을 수 없으므로, 선형-가우시안 상태공간 모형의
            # **해석적 사후분포(Kalman filter = 해당 모형의 정확한 베이즈 사후)**
            # 를 폴백으로 제공한다. fit() 으로 posterior 가 캐시되면 그쪽이 우선한다.
            logger.debug(
                "BayesFactorFeatures.compute called before fit(); using the "
                "analytic Kalman posterior fallback (no MCMC)."
            )
            return self._analytic_features(log_rets)

        return self._posterior_features(self._posterior, log_rets)

    def _analytic_features(self, log_rets: np.ndarray) -> Dict:
        """선형-가우시안 상태공간 모형의 해석적(칼만) 사후 피처.

        모형( ``_fit`` 과 동일 구조, 단순화 ):
            momentum[t] = momentum[t-1] + N(0, Q)
            obs[t]      = momentum[t] + N(0, R)
        Q/R 을 데이터에서 적률추정(모멘트) 으로 잡고 칼만 필터를 돌려
        가우시안 사후 평균/분산을 얻는다. MCMC(fit) 없이도 유한한 값이 나오며,
        표본이 달라지면(=날짜가 달라지면) 값도 달라진다.

        Returns:
            ``compute`` 와 동일한 4개 키. 입력이 퇴화하면 0.0 기본값.
        """
        defaults = {name: 0.0 for name in self.FEATURE_NAMES}
        y = np.asarray(log_rets, dtype=float)
        if y.size < 3:
            return dict(defaults)

        # 관측분산 R, 상태분산 Q 적률추정
        # E[(y_t - y_{t-1})^2] = 2R + Q  →  Q = var(diff) - 2R
        r = float(np.var(y))
        if not np.isfinite(r) or r <= 0:
            return dict(defaults)
        d = np.diff(y)
        q = float(np.var(d)) - 2.0 * r if d.size >= 2 else r
        q = max(q, 1e-12)  # 상태분산은 양수 제약
        q = min(q, 1e3 * r)

        # 칼만 필터 (초기: 첫 관측, 사전분산 R)
        m = float(y[0])
        p = r
        states = [m]
        for obs in y[1:]:
            m_pred = m
            p_pred = p + q
            k = p_pred / (p_pred + r)
            m = m_pred + k * (float(obs) - m_pred)
            p = (1.0 - k) * p_pred
            states.append(m)

        states_arr = np.asarray(states)
        window = min(5, states_arr.size)
        return {
            "bayes_momentum_1d": float(states_arr[-1]),
            "bayes_momentum_5d": float(np.mean(states_arr[-window:])),
            "bayes_volatility": float(np.sqrt(r) * np.sqrt(252.0)),
            "bayes_gain_uncertainty": float(np.sqrt(p)),
        }

    def fit(self, close_prices) -> "BayesFactorFeatures":
        """Fit the state-space model offline and cache the posterior.

        This is the ONLY place NUTS MCMC runs. It fits once on the supplied
        close prices and caches the posterior so subsequent ``compute`` calls
        are cheap forward passes. Returns ``self`` for chaining.

        Args:
            close_prices: iterable of close prices (list/array/Series).

        Returns:
            ``self`` with the posterior cached.
        """
        if close_prices is None or len(close_prices) < 5:
            logger.warning("fit() needs >= 5 close prices; posterior not cached")
            return self

        prices = np.array([float(c) for c in close_prices])
        log_rets = np.diff(np.log(prices))

        if len(log_rets) < 3:
            logger.warning("fit() needs >= 3 log-returns; posterior not cached")
            return self

        if not _NUMPYRO_AVAILABLE:
            logger.warning("numpyro unavailable; posterior not cached")
            return self

        try:
            self._posterior = self._fit(log_rets)
        except Exception as e:
            logger.warning(f"Bayes factor fit failed ({e}); posterior not cached")
            self._posterior = None
        return self

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _fit(self, log_rets: np.ndarray) -> Dict:
        """Fit the state-space model with NUTS and return posterior samples."""
        n = len(log_rets)

        def model():
            sigma_obs = numpyro.sample("sigma_obs", dist.HalfNormal(0.01))
            sigma_state = numpyro.sample("sigma_state", dist.HalfNormal(0.01))
            init_momentum = numpyro.sample("init_momentum", dist.Normal(0.0, 0.01))
            momentum = self._build_momentum_prior(init_momentum, sigma_state, n)
            numpyro.sample("obs", dist.Normal(momentum, sigma_obs), obs=jnp.asarray(log_rets, dtype=jnp.float32))

        rng_key = random.PRNGKey(self.seed)
        mcmc = MCMC(
            NUTS(model),
            num_warmup=self.num_warmup,
            num_samples=self.num_samples,
            num_chains=1,
        )
        mcmc.run(rng_key)
        return mcmc.get_samples()

    def _posterior_features(self, posterior: Dict, log_rets: np.ndarray) -> Dict:
        """Derive the 4 bayes features from posterior samples."""
        momentum_samples = np.asarray(posterior["momentum"])  # shape (num_samples, n)
        sigma_obs_samples = np.asarray(posterior["sigma_obs"])

        # Posterior mean/std of the latest momentum state.
        latest_mean = float(np.mean(momentum_samples[:, -1]))
        latest_std = float(np.std(momentum_samples[:, -1]))

        # 5-day average momentum (posterior mean over the last 5 states).
        n = momentum_samples.shape[1]
        window = min(5, n)
        five_day = float(np.mean(momentum_samples[:, -window:]))

        # Annualized volatility from posterior sigma_obs (sqrt(252) scaling).
        vol = float(np.mean(sigma_obs_samples) * np.sqrt(252))

        return {
            "bayes_momentum_1d": latest_mean,
            "bayes_momentum_5d": five_day,
            "bayes_volatility": vol,
            "bayes_gain_uncertainty": latest_std,
        }
