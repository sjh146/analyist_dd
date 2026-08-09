"""
factor_strategy_base - 팩터 전략 공용 베이스 (유니버스→팩터랭크→시그널).

Value/Quality/Momentum/LowVol/MultiFactor 전략이 공유하는 흐름:
1) 리밸런싱 게이트(rebalance_interval_days, 기본 63)
2) 유니버스 필터(T2 filter_universe)
3) 서브클래스의 팩터 점수 → 크로스섹션 랭크
4) 상위 top_n(기본 30) buy + 보유 중 이탈 종목 sell

point-in-time 스냅샷/가격 시리즈를 쓰므로 look-ahead bias가 없다.
"""

import logging
from abc import abstractmethod
from datetime import date, datetime
from typing import Dict, List, Optional, Set

from app.strategies.base_strategy import BaseStrategy
from app.factors.universe import filter_universe
from app.factors.financial_snapshot import FinancialSnapshot
from app.factors.factor_base import rank_scores, normalize_rank_confidence

logger = logging.getLogger(__name__)


class FactorStrategyBase(BaseStrategy):
    """Shared flow for book factor strategies (top-30 equal weight)."""

    def __init__(self, name: str, storage, config: Optional[Dict] = None):
        super().__init__(name, storage, config)
        self.top_n = int(self.config.get("top_n", 30))
        self.rebalance_interval_days = int(self.config.get("rebalance_interval_days", 63))
        self.rebalance_anchor = self.config.get("rebalance_anchor", "2024-03-31")

    @abstractmethod
    def _factor_scores(self, universe, snapshot, asof_date, cap_map) -> Dict[str, Optional[float]]:
        """Per-stock factor scores; None values are excluded from ranking.

        Subclass decides the score direction; see _rank.
        """

    def analyze(self, asof_date=None) -> List[Dict]:
        """Produce buy (top_n) / sell (dropped-out held) signals."""
        if asof_date is None:
            asof_date = date.today()
        elif not hasattr(asof_date, "strftime"):
            asof_date = datetime.strptime(str(asof_date), "%Y-%m-%d").date()
        if not self._is_rebalance_day(asof_date):
            return []

        stocks = self.storage.get_all_stocks()
        universe = filter_universe(self.storage, stocks, asof_date=asof_date)
        if not universe:
            return []

        caps = self.storage.get_market_caps()
        cap_map = {code: caps.get(code) for code in universe}
        snapshot = FinancialSnapshot(self.storage)
        scores = self._factor_scores(universe, snapshot, asof_date, cap_map)

        ranked = self._rank(scores)
        if not ranked:
            return []

        ranked_codes = sorted(ranked, key=ranked.get)
        top = set(ranked_codes[: self.top_n])

        signals = []
        for code, rank in ranked.items():
            if code in top:
                signals.append({
                    "action": "buy",
                    "stock_code": code,
                    "price": 0,
                    "reason": f"{self.name} top-{self.top_n}",
                    "strategy_name": self.name,
                    "confidence": normalize_rank_confidence(rank, len(ranked)),
                })

        held = self._get_held_codes()
        for code in sorted(held - top):
            signals.append({
                "action": "sell",
                "stock_code": code,
                "price": 0,
                "reason": f"{self.name} dropped out of top-{self.top_n}",
                "strategy_name": self.name,
                "confidence": 0.6,
            })

        return signals

    def _rank(self, scores: Dict[str, Optional[float]]) -> Dict[str, int]:
        """Cross-sectional rank (rank 1 = best) over non-None scores.

        Subclass overrides to control 'lower is better' (value) vs
        'higher is better' (quality/momentum/low-vol) semantics.
        """
        return rank_scores(scores, ascending=True)

    def _is_rebalance_day(self, asof_date) -> bool:
        """True when asof_date is on the rebalance cycle (interval in days).

        interval <= 0 disables the gate (rebalance every run, for tests).
        """
        if self.rebalance_interval_days <= 0:
            return True
        anchor = datetime.strptime(self.rebalance_anchor, "%Y-%m-%d").date()
        days = (asof_date - anchor).days
        return days >= 0 and days % self.rebalance_interval_days == 0

    def _get_held_codes(self) -> Set[str]:
        """Stock codes currently held (positions), empty on any error."""
        try:
            positions = self.storage.get_positions() or []
        except Exception as e:
            logger.debug(f"get_positions unavailable: {e}")
            return set()
        return {p.get("stock_code") for p in positions if p.get("stock_code")}
