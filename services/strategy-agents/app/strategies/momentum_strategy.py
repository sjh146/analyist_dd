"""
momentum_strategy - MomentumStrategy (모멘텀 전략, 강환국『하면 된다! 퀀트투자』).

market_data.close_price 기준 세 팩터 (각각 높을수록 좋음):
① 12-1 모멘텀   = (P_t / P_{t-252}) - (P_t / P_{t-21})   (252=1년, 21=1개월 거래일 근사)
② 3-6 모멘텀    = (P_t / P_{t-63}) - (P_t / P_{t-126})
③ 52주 고가 근접 = P_t / MAX(P_{t-252..t})

크로스섹션 랭크 → 동일비중 평균 → 상위 30 buy / 이탈 sell.
long-only: short 금지, sell은 long 종료만. 가격 이력 253일 미만(1년 미만) 종목은 제외.
point-in-time 가격 시리즈(get_price_series_asof)를 쓰므로 look-ahead bias가 없다.
"""

from typing import Dict, List, Optional, Tuple

from app.factors.factor_base import rank_scores
from app.strategies.factor_strategy_base import FactorStrategyBase

_ONE_YEAR = 252  # 1년 거래일 근사
_ONE_MONTH = 21  # 1개월 거래일 근사


class MomentumStrategy(FactorStrategyBase):
    def __init__(self, storage, config: Optional[Dict] = None):
        super().__init__("momentum_factor", storage, config)

    def _factor_scores(self, universe, snapshot, asof_date, cap_map) -> Dict[str, Optional[float]]:
        factors = {}
        for code in universe:
            series = self.storage.get_price_series_asof(code, days=_ONE_YEAR + 1, asof_date=asof_date)
            m12_1, m3_6, m52w = self._momentum_factors(series)
            if any(v is not None for v in (m12_1, m3_6, m52w)):
                factors[code] = (m12_1, m3_6, m52w)

        m12_1_rank = rank_scores({c: f[0] for c, f in factors.items()}, ascending=False)
        m3_6_rank = rank_scores({c: f[1] for c, f in factors.items()}, ascending=False)
        m52w_rank = rank_scores({c: f[2] for c, f in factors.items()}, ascending=False)

        combined = {}
        for code in set(m12_1_rank) | set(m3_6_rank) | set(m52w_rank):
            ranks = [r[code] for r in (m12_1_rank, m3_6_rank, m52w_rank) if code in r]
            combined[code] = sum(ranks) / len(ranks)
        return combined

    def _momentum_factors(self, series: List[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """12-1/3-6/52주 팩터 계산. 이력 부족(253일 미만)이거나 기준 가격이 0·음수면 해당 팩터 None."""
        if len(series) < _ONE_YEAR + 1:
            return None, None, None
        p_t = series[-1]
        if p_t <= 0:
            return None, None, None
        m12_1 = self._ratio_diff(p_t, series[-_ONE_YEAR - 1], series[-_ONE_MONTH - 1])
        m3_6 = self._ratio_diff(p_t, series[-63 - 1], series[-126 - 1])
        peak = max(series[-_ONE_YEAR:])
        m52w = p_t / peak if peak > 0 else None
        return m12_1, m3_6, m52w

    @staticmethod
    def _ratio_diff(p_t: float, p_old: float, p_recent: float) -> Optional[float]:
        """(P_t / P_old) - (P_t / P_recent); 기준 가격이 0·음수면 None."""
        if p_old <= 0 or p_recent <= 0:
            return None
        return (p_t / p_old) - (p_t / p_recent)
