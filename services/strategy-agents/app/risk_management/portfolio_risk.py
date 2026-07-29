"""
Portfolio Risk Manager
Checks correlation, concentration, and drawdown limits.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class PortfolioRiskManager:
    def __init__(self, pg_storage=None):
        self.pg_storage = pg_storage

    def check_correlation(
        self, stock_code: str, current_positions: List[Dict],
        threshold: float = 0.8, window: int = 20,
    ) -> Dict:
        if self.pg_storage is None or not current_positions:
            return {"approved": True, "reason": "No existing positions"}
        for pos in current_positions:
            other = pos.get("stock_code")
            if not other or other == stock_code:
                continue
            rets_a = self._get_returns(stock_code, window)
            rets_b = self._get_returns(other, window)
            if len(rets_a) < 5 or len(rets_b) < 5:
                continue
            corr = self._pearson(rets_a, rets_b)
            if corr > threshold:
                return {"approved": False, "reason": f"High correlation with {other}"}
        return {"approved": True, "reason": "OK"}

    def check_concentration(
        self, stock_code: str, current_positions: List[Dict],
        max_single: float = 0.10, max_sector: float = 0.30,
    ) -> Dict:
        if self.pg_storage is None:
            return {"approved": True, "reason": "No pg_storage"}
        total_value = sum(
            p.get("quantity", 0) * p.get("avg_buy_price", 0)
            for p in current_positions
        )
        if total_value <= 0:
            return {"approved": True, "reason": "No existing value"}
        new_value = 10_000_000
        if new_value / (total_value + new_value) > max_single:
            return {"approved": False, "reason": f"Position would exceed {max_single:.0%} single stock limit"}
        sector = self._get_sector(stock_code)
        if sector:
            sector_value = new_value + sum(
                p.get("quantity", 0) * p.get("avg_buy_price", 0)
                for p in current_positions
                if self._get_sector(p.get("stock_code", "")) == sector
            )
            if sector_value / (total_value + new_value) > max_sector:
                return {"approved": False, "reason": f"Position would exceed {max_sector:.0%} sector limit"}
        return {"approved": True, "reason": "OK"}

    def check_drawdown(
        self, portfolio_history: List[float], max_drawdown: float = 0.15,
    ) -> Dict:
        if not portfolio_history or len(portfolio_history) < 2:
            return {"approved": True, "reason": "Insufficient history"}
        running_max = portfolio_history[0]
        max_dd = 0.0
        for val in portfolio_history:
            if val > running_max:
                running_max = val
            dd = (running_max - val) / running_max
            if dd > max_dd:
                max_dd = dd
        if max_dd > max_drawdown:
            return {"approved": False, "reason": f"Drawdown exceeded: {max_dd:.2%} > {max_drawdown:.0%}"}
        return {"approved": True, "reason": "OK"}

    def check_all(
        self, stock_code: str, current_positions: List[Dict],
        portfolio_history: Optional[List[float]] = None,
    ) -> Dict:
        corr = self.check_correlation(stock_code, current_positions)
        conc = self.check_concentration(stock_code, current_positions)
        dd = self.check_drawdown(portfolio_history) if portfolio_history else {"approved": True, "reason": "No history"}
        approved = corr["approved"] and conc["approved"] and dd["approved"]
        return {
            "approved": approved,
            "correlation": corr,
            "concentration": conc,
            "drawdown": dd,
        }

    def _get_returns(self, stock_code: str, window: int) -> List[float]:
        if not self.pg_storage:
            return []
        prices = self.pg_storage.get_price_series(stock_code, window)
        if len(prices) < 2:
            return []
        return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]

    def _pearson(self, x: List[float], y: List[float]) -> float:
        n = min(len(x), len(y))
        if n < 3:
            return 0.0
        x, y = x[:n], y[:n]
        mx = sum(x) / n
        my = sum(y) / n
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        den_x = sum((xi - mx) ** 2 for xi in x)
        den_y = sum((yi - my) ** 2 for yi in y)
        den = (den_x * den_y) ** 0.5
        if den == 0:
            return 0.0
        return num / den

    def _get_sector(self, stock_code: str) -> Optional[str]:
        if not self.pg_storage:
            return None
        stocks = self.pg_storage.get_all_stocks()
        for s in stocks:
            if s.get("stock_code") == stock_code:
                return s.get("sector")
        return None
