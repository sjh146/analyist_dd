"""
quality_strategy - QualityStrategy (퀄리티 전략, 강환국『하면 된다! 퀀트투자』).

세 팩터를 크로스섹션 랭크(높을수록 좋음)해 동일비중 평균으로 상위 30을 산다:
① ROE 2개 분기 평균 (point-in-time)
② GP/A = gross_profit / total_assets (gross_profit 없으면 해당 팩터 제외)
③ 이익 안정성 = 최근 4분기 net_income 합 / 직전 4분기 합 - 1 (YoY 증가율)

팩터가 없는 종목은 보유 팩터만으로 결합(비중 0)해도 안전하게 랭크된다.
"""

from typing import Dict, Optional

from app.factors.value_ratios import compute_ratios
from app.factors.factor_base import rank_scores
from app.strategies.factor_strategy_base import FactorStrategyBase


class QualityStrategy(FactorStrategyBase):
    def __init__(self, storage, config: Optional[Dict] = None):
        super().__init__("quality_factor", storage, config)

    def _factor_scores(self, universe, snapshot, asof_date, cap_map) -> Dict[str, Optional[float]]:
        """팩터별 랭크 평균(낮을수록 좋음) 반환 → 베이스 랭크가 1등=최고 처리."""
        ratios = compute_ratios(snapshot, cap_map, asof_date=asof_date)
        roe = {c: self._avg_roe(snapshot, c, asof_date) for c in universe}
        gpa = {c: ratios[c]["gpa"] for c in ratios}
        stability = {c: self._earnings_stability(snapshot, c, asof_date) for c in universe}

        roe_rank = rank_scores(roe, ascending=False)
        gpa_rank = rank_scores(gpa, ascending=False)
        stab_rank = rank_scores(stability, ascending=False)

        combined = {}
        for code in set(roe_rank) | set(gpa_rank) | set(stab_rank):
            ranks = [r[code] for r in (roe_rank, gpa_rank, stab_rank) if code in r]
            combined[code] = sum(ranks) / len(ranks)
        return combined

    def _avg_roe(self, snapshot, code, asof_date) -> Optional[float]:
        """최근 2개 분기 point-in-time ROE 평균, 유효 ROE 없으면 None."""
        rows = snapshot.get_history(code, asof_date=asof_date, n_quarters=2)
        roes = []
        for row in rows:
            ni = self._positive(row.get("net_income"))
            eq = self._positive(row.get("total_equity"))
            if ni and eq:
                roes.append(ni / eq)
        return sum(roes) / len(roes) if roes else None

    def _earnings_stability(self, snapshot, code, asof_date) -> Optional[float]:
        """최근 4분기 합 / 직전 4분기 합 - 1, 8분기 미만이거나 직전 합<=0이면 None."""
        rows = snapshot.get_history(code, asof_date=asof_date)
        if len(rows) < 8:
            return None
        prev = sum(float(r.get("net_income") or 0) for r in rows[:4])
        last = sum(float(r.get("net_income") or 0) for r in rows[4:])
        if prev <= 0:
            return None
        return last / prev - 1.0

    @staticmethod
    def _positive(value) -> Optional[float]:
        if value is None:
            return None
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None
