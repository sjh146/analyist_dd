"""
value_strategy - ValueStrategy (가치 전략, 강환국『하면 된다! 퀀트투자』).

유니버스(T2) 내 종목을 대상으로 PER/PBR/PSR 크로스섹션 랭크를 계산한다.
- mode="per_pbr": PER·PBR 각각 '낮을수록 좋은' 랭크 → 동일비중 평균 랭크 → 상위 30
- mode="psr":      낮은 PSR 상위 30 (대안)

리밸런싱은 사용자 관례(분기, 3·6·9·12월 말)의 t-1 대용으로 config
`rebalance_interval_days`(기본 63) 주기로 판단한다. point-in-time 재무
스냅샷 + 최신 market_cap을 사용하므로 look-ahead bias가 없다.
"""

import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Set

from app.strategies.base_strategy import BaseStrategy
from app.factors.universe import filter_universe
from app.factors.financial_snapshot import FinancialSnapshot
from app.factors.value_ratios import compute_ratios
from app.factors.factor_base import rank_scores, normalize_rank_confidence

logger = logging.getLogger(__name__)


class ValueStrategy(BaseStrategy):
    def __init__(self, storage, mode: str = "per_pbr", config: Optional[Dict] = None):
        super().__init__("value_factor", storage, config)
        self.mode = self.config.get("mode", mode)
        self.top_n = int(self.config.get("top_n", 30))
        self.rebalance_interval_days = int(self.config.get("rebalance_interval_days", 63))
        self.rebalance_anchor = self.config.get("rebalance_anchor", "2024-03-31")

    def analyze(self, asof_date=None) -> List[Dict]:
        """Run the value strategy and produce buy/sell signals.

        On a rebalance day: top-`top_n` ranked stocks get buy signals and
        previously held stocks that dropped out of the top-`top_n` get sell.
        On non-rebalance days no signals are emitted.

        Args:
            asof_date: 'YYYY-MM-DD' or date. Defaults to today (for tests).
        """
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
        ratios = compute_ratios(snapshot, cap_map, asof_date=asof_date)

        combined_rank = self._rank(ratios)
        if not combined_rank:
            return []

        ranked_codes = sorted(combined_rank, key=combined_rank.get)
        top = set(ranked_codes[: self.top_n])

        signals = []
        for code, rank in combined_rank.items():
            if code in top:
                signals.append({
                    "action": "buy",
                    "stock_code": code,
                    "price": 0,
                    "reason": f"Value top-{self.top_n} ({self.mode})",
                    "strategy_name": self.name,
                    "confidence": normalize_rank_confidence(rank, len(combined_rank)),
                })

        held = self._get_held_codes()
        for code in sorted(held - top):
            signals.append({
                "action": "sell",
                "stock_code": code,
                "price": 0,
                "reason": f"Value dropped out of top-{self.top_n} ({self.mode})",
                "strategy_name": self.name,
                "confidence": 0.6,
            })

        return signals

    def _rank(self, ratios: Dict[str, Dict[str, Optional[float]]]) -> Dict[str, int]:
        """Cross-sectional rank; rank 1 = best (lowest value wins)."""
        if self.mode == "psr":
            psr = {c: r["psr"] for c, r in ratios.items()}
            return rank_scores(psr, ascending=True)

        per = {c: r["per"] for c, r in ratios.items()}
        pbr = {c: r["pbr"] for c, r in ratios.items()}
        per_rank = rank_scores(per, ascending=True)
        pbr_rank = rank_scores(pbr, ascending=True)
        common = set(per_rank) & set(pbr_rank)
        if not common:
            return {}
        avg = {c: (per_rank[c] + pbr_rank[c]) / 2.0 for c in common}
        return rank_scores(avg, ascending=True)

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
