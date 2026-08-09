"""
multifactor_strategy - MultiFactorStrategy (멀티팩터 결합, 강환국『하면 된다! 퀀트투자』).

가치(T5)·퀄리티(T6)·모멘텀(T7)·저변동(T8) 팩터를 크로스섹션 Z-score((x-μ)/σ)로
방향 통일(양(+) = 좋음) 후 동일가중 합산 → 상위 20종목 buy / 이탈 sell.
분산 0인 팩터는 자동 제외(방어), 가중치 튜닝 없음(책의 동일가중 그대로).
"""

from typing import Dict, Optional

from app.factors.value_ratios import compute_ratios
from app.factors.factor_base import rank_scores, zscore_scores
from app.strategies.factor_strategy_base import FactorStrategyBase
from app.strategies.quality_strategy import QualityStrategy
from app.strategies.momentum_strategy import MomentumStrategy
from app.strategies.lowvol_strategy import LowVolatilityStrategy

_ONE_YEAR = 252

# (팩터명, 높을수록 좋은가) — 낮은 PER/PBR/PSR/변동성/베타는 False로 방향 통일
_FACTOR_SPECS = [
    ("per", False), ("pbr", False), ("psr", False),
    ("roe", True), ("gpa", True), ("stability", True),
    ("m12_1", True), ("m3_6", True), ("m52w", True),
    ("vol", False), ("beta", False),
]


class MultiFactorStrategy(FactorStrategyBase):
    def __init__(self, storage, config: Optional[Dict] = None):
        super().__init__("multifactor", storage, config)
        self.top_n = int(self.config.get("top_n", 20))
        self.rebalance_interval_days = int(self.config.get("rebalance_interval_days", 21))
        self.quality = QualityStrategy(storage, config=config)
        self.momentum = MomentumStrategy(storage, config=config)
        self.lowvol = LowVolatilityStrategy(storage, config=config)

    def _rank(self, scores):
        """합산 Z-score는 높을수록 좋음 → rank 1 = 최고 점수."""
        return rank_scores(scores, ascending=False)

    def _factor_scores(self, universe, snapshot, asof_date, cap_map) -> Dict[str, Optional[float]]:
        factors = {c: {} for c in universe}

        ratios = compute_ratios(snapshot, cap_map, asof_date=asof_date)
        for c, r in ratios.items():
            factors[c].update(per=r["per"], pbr=r["pbr"], psr=r["psr"], gpa=r["gpa"])

        for c in universe:
            factors[c]["roe"] = self.quality._avg_roe(snapshot, c, asof_date)
            factors[c]["stability"] = self.quality._earnings_stability(snapshot, c, asof_date)

        for c in universe:
            series = self.storage.get_price_series_asof(c, days=_ONE_YEAR + 1, asof_date=asof_date)
            m12_1, m3_6, m52w = self.momentum._momentum_factors(series)
            factors[c].update(m12_1=m12_1, m3_6=m3_6, m52w=m52w)

        rets = {}
        for c in universe:
            series = self.storage.get_price_series_asof(c, days=_ONE_YEAR + 1, asof_date=asof_date)
            ret = self.lowvol._log_returns(series)[-_ONE_YEAR:]
            if ret:
                rets[c] = ret
        market = self.lowvol._market_returns(rets, asof_date)
        for c in universe:
            ret = rets.get(c)
            if not ret:
                continue
            factors[c]["vol"] = self.lowvol._annualized_vol(ret)
            beta = self.lowvol._beta(ret, market) if market else None
            factors[c]["beta"] = beta if beta is not None and beta >= 0 else None

        return self._combine_z(factors)

    def _combine_z(self, factors: Dict[str, Dict[str, Optional[float]]]) -> Dict[str, float]:
        """팩터별 Z-score(양(+) = 좋음) 동일가중 합산; 분산 0 팩터는 zscore_scores가 {} 반환으로 자동 제외."""
        combined = {}
        for name, higher in _FACTOR_SPECS:
            values = {c: f[name] for c, f in factors.items() if f.get(name) is not None}
            z = zscore_scores(values, higher_is_better=higher)
            for c, zz in z.items():
                combined[c] = combined.get(c, 0.0) + zz
        return combined
