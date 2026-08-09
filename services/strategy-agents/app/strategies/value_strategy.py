"""
value_strategy - ValueStrategy (가치 전략, 강환국『하면 된다! 퀀트투자』).

유니버스(T2) 내 종목에 대해 mode별 랭크로 상위 30을 산다:
- mode="per_pbr": PER·PBR 각각 '낮을수록 좋은' 랭크 → 동일비중 평균 랭크
- mode="psr":      낮은 PSR 단일 랭크 (대안)

point-in-time 재무 스냅샷 + 최신 market_cap 기반이라 look-ahead bias가 없다.
"""

from typing import Dict, Optional

from app.factors.value_ratios import compute_ratios
from app.factors.factor_base import rank_scores
from app.strategies.factor_strategy_base import FactorStrategyBase


class ValueStrategy(FactorStrategyBase):
    def __init__(self, storage, mode: str = "per_pbr", config: Optional[Dict] = None):
        super().__init__("value_factor", storage, config)
        self.mode = self.config.get("mode", mode)

    def _factor_scores(self, universe, snapshot, asof_date, cap_map) -> Dict[str, Optional[float]]:
        """낮을수록 좋은 값 반환 → 베이스 랭크(ascending)가 1등=최저 처리."""
        ratios = compute_ratios(snapshot, cap_map, asof_date=asof_date)
        if self.mode == "psr":
            return {c: r["psr"] for c, r in ratios.items()}
        per_rank = rank_scores({c: r["per"] for c, r in ratios.items()}, ascending=True)
        pbr_rank = rank_scores({c: r["pbr"] for c, r in ratios.items()}, ascending=True)
        common = set(per_rank) & set(pbr_rank)
        return {c: (per_rank[c] + pbr_rank[c]) / 2.0 for c in common}
