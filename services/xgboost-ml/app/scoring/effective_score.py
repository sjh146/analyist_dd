"""Shared ``effective_score`` computation (Phase 4).

``effective_score = calibrated_prob - kappa * sigma``

This is the ONE score consumed identically by the screener threshold
(``CONFIDENCE_THRESHOLD``) and the backtester signal gate (``>= 0.65``), so a
single correction in one place keeps both pipelines consistent.

It imports from the calibration layer (``BayesianCalibrator``) and the GP
uncertainty layer (``GPUncertainty``). If the GPR is unavailable (``sigma``
missing), ``EffectiveScore.score`` falls back to ``calibrated_prob`` and raises
a warning instead of crashing.

The screener's filter+sort logic (``score_and_filter_candidates``) and the
backtester's gate decision (``should_buy``) live here so they can be unit-tested
in the container and shared verbatim by both entry points.
"""

import logging
from typing import Dict, List, Optional, Sequence

from app.calibration.bayesian_calibration import BayesianCalibrator
from app.uncertainty.gp_uncertainty import GPUncertainty

logger = logging.getLogger(__name__)

# κ-sweep (Todo 3) showed κ=0.3 gives the best Sharpe. Default penalty.
DEFAULT_KAPPA = 0.3


def compute_effective_score(
    calibrated_prob: float, sigma: float, kappa: float = DEFAULT_KAPPA
) -> float:
    """Pure composition: ``effective_score = calibrated_prob - kappa * sigma``.

    Delegates to ``GPUncertainty.effective_score`` so the formula lives in one
    place.
    """
    return GPUncertainty.effective_score(calibrated_prob, sigma, kappa)


class EffectiveScore:
    """Compute ``effective_score`` from a calibrator + GP uncertainty model.

    Parameters
    ----------
    calibrator : optional
        A fitted calibrator exposing ``calibrate(probs)`` returning a dict with
        ``calibrated_probability`` (and ``calibration_uncertainty``). If None,
        the raw prob is used as the calibrated probability.
    gp : optional
        A fitted ``GPUncertainty`` exposing ``predict_std(feature_vec)``. If
        None (GPR unavailable), ``effective_score`` falls back to
        ``calibrated_prob`` and a warning is raised.
    kappa : float
        Uncertainty penalty coefficient (default 0.3).
    """

    def __init__(
        self,
        calibrator: Optional[BayesianCalibrator] = None,
        gp: Optional[GPUncertainty] = None,
        kappa: float = DEFAULT_KAPPA,
    ) -> None:
        self.calibrator = calibrator
        self.gp = gp
        self.kappa = kappa

    def score(
        self,
        prob: float,
        feature_vec: Optional[Sequence[float]] = None,
        sigma: Optional[float] = None,
    ) -> Dict[str, Optional[float]]:
        """Return ``effective_score`` plus the calibrated prob and epistemic std.

        Parameters
        ----------
        prob : float
            Raw ensemble up-probability in [0, 1].
        feature_vec : optional
            Low-dimensional feature vector for the GP ``predict_std``. Ignored
            if ``sigma`` is supplied directly.
        sigma : optional
            Pre-computed epistemic std. If None, it is derived from ``gp`` and
            ``feature_vec``.

        Returns
        -------
        dict with keys ``effective_score``, ``calibrated_probability``,
        ``epistemic_std``. ``epistemic_std`` is None when the GPR is
        unavailable (fallback path).
        """
        # 1. Calibrated probability (calibration layer).
        if self.calibrator is not None:
            cal = self.calibrator.calibrate(prob)
            calibrated_prob = float(cal["calibrated_probability"])
        else:
            calibrated_prob = float(prob)

        # 2. Epistemic std (GP uncertainty layer).
        if sigma is None and self.gp is not None and feature_vec is not None:
            sigma = float(self.gp.predict_std(feature_vec))

        if sigma is None:
            # GPR unavailable -> fall back to calibrated_prob, warn (not crash).
            logger.warning(
                "GPR unavailable (sigma missing) — effective_score falls back to "
                "calibrated_prob=%.4f (no uncertainty penalty).",
                calibrated_prob,
            )
            return {
                "effective_score": calibrated_prob,
                "calibrated_probability": calibrated_prob,
                "epistemic_std": None,
            }

        effective = compute_effective_score(calibrated_prob, sigma, self.kappa)
        return {
            "effective_score": effective,
            "calibrated_probability": calibrated_prob,
            "epistemic_std": sigma,
        }


def score_and_filter_candidates(
    raw_candidates: List[dict],
    effective_scorer: EffectiveScore,
    use_effective_score: bool,
    threshold: float,
) -> List[dict]:
    """Score raw candidates, filter by threshold, sort by the active key.

    This is the screener's filter+sort logic, extracted so it can be unit-tested
    and shared. It captures the sort key and the gate decision.

    Parameters
    ----------
    raw_candidates : list of dicts
        Each dict has ``stock_code``, ``stock_name``, ``sector``, ``prob`` and
        optionally ``low_dim_vec`` (the low-dim feature vector for the GP).
    effective_scorer : EffectiveScore
        Used to compute ``effective_score`` when the flag is on.
    use_effective_score : bool
        When True, filter on ``effective_score >= threshold`` and sort by
        ``effective_score`` desc. When False, filter on ``prob >= threshold``
        and sort by ``confidence`` desc (old behavior).
    threshold : float
        The confidence/effective_score threshold.

    Returns
    -------
    list of candidate dicts, sorted by the active key.
    """
    scored: List[dict] = []
    for c in raw_candidates:
        prob = float(c["prob"])
        if use_effective_score:
            result = effective_scorer.score(prob, feature_vec=c.get("low_dim_vec"))
            effective = result["effective_score"]
            if effective >= threshold:
                scored.append(
                    {
                        "stock_code": c["stock_code"],
                        "stock_name": c["stock_name"],
                        "sector": c["sector"],
                        "confidence": round(prob, 4),
                        "effective_score": round(effective, 4),
                        "calibrated_probability": round(
                            result["calibrated_probability"], 4
                        ),
                        "epistemic_std": (
                            round(result["epistemic_std"], 4)
                            if result["epistemic_std"] is not None
                            else None
                        ),
                        "expected_return": round((prob - 0.5) * 2.0 * 100.0, 2),
                    }
                )
        else:
            if prob >= threshold:
                scored.append(
                    {
                        "stock_code": c["stock_code"],
                        "stock_name": c["stock_name"],
                        "sector": c["sector"],
                        "confidence": round(prob, 4),
                        "expected_return": round((prob - 0.5) * 2.0 * 100.0, 2),
                    }
                )

    if use_effective_score:
        scored.sort(key=lambda x: x["effective_score"], reverse=True)
    else:
        scored.sort(key=lambda x: x["confidence"], reverse=True)
    return scored


def should_buy(
    prob: float,
    effective_score: Optional[float],
    use_effective_score: bool,
    threshold: float = 0.65,
) -> bool:
    """Backtester buy-signal gate decision.

    When ``use_effective_score`` is True, gate on ``effective_score >=
    threshold``; otherwise gate on ``prob >= threshold`` (old behavior).
    """
    if use_effective_score:
        return effective_score is not None and effective_score >= threshold
    return prob >= threshold
