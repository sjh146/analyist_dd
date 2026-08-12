"""Shared effective_score computation (Phase 4).

``effective_score = calibrated_prob - kappa * sigma``, consumed identically by
the screener threshold and the backtester signal gate.
"""

from app.scoring.effective_score import (
    DEFAULT_KAPPA,
    EffectiveScore,
    compute_effective_score,
    score_and_filter_candidates,
    should_buy,
)

__all__ = [
    "DEFAULT_KAPPA",
    "EffectiveScore",
    "compute_effective_score",
    "score_and_filter_candidates",
    "should_buy",
]
