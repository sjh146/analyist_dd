"""
factor_base - 크로스섹션 랭크/Z-score 공용 헬퍼.

책 규칙(상위 30 동일가중, Z-score 동일가중 결합)의 계산 기반.
랭크 1 = best. Z-score는 항상 '양(+) = 좋음' 방향으로 정규화한다.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


def rank_scores(scores: Dict[str, Optional[float]], ascending: bool = True) -> Dict[str, int]:
    """Cross-sectional rank; rank 1 = best.

    ascending=True -> lowest value gets rank 1 (e.g. low PER/PBR).
    ascending=False -> highest value gets rank 1 (e.g. high ROE).
    None values are excluded from ranking.
    """
    valid = {k: v for k, v in scores.items() if v is not None}
    if not valid:
        return {}
    ordered = sorted(valid.items(), key=lambda kv: kv[1], reverse=not ascending)
    return {code: i + 1 for i, (code, _) in enumerate(ordered)}


def zscore_scores(scores: Dict[str, Optional[float]], higher_is_better: bool = True) -> Dict[str, float]:
    """Cross-sectional z-score (x-mean)/std, direction-normalized so positive = better.

    Degenerate factor (std < 1e-8) returns {} so callers skip it safely
    (anti over-fit defense, plan 심층분석 4).
    """
    valid = {k: v for k, v in scores.items() if v is not None}
    if len(valid) < 2:
        return {}
    vals = list(valid.values())
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    if std < 1e-8:
        return {}
    z = {k: (v - mean) / std for k, v in valid.items()}
    if not higher_is_better:
        z = {k: -v for k, v in z.items()}
    return z


def normalize_rank_confidence(rank: int, total: int) -> float:
    """Map rank (1 = best) into confidence [0.5, 0.95]."""
    if total <= 0:
        return 0.5
    return round(0.5 + 0.45 * (1.0 - (rank - 1) / total), 4)


class FactorBase(ABC):
    """Abstract factor computation: compute() returns per-stock factor dicts."""

    def __init__(self, storage):
        self.storage = storage

    @abstractmethod
    def compute(self, stocks: List[Dict], asof_date=None) -> List[Dict]:
        """Compute factor values for the given stock list."""
