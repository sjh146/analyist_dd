"""
Theme Trading Strategy
Finds stock groups via pgvector similarity, identifies leaders/laggards.
"""

import numpy as np
import logging
from typing import List, Dict
from app.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class ThemeStrategy(BaseStrategy):
    def __init__(self, storage):
        super().__init__("theme_trading", storage)
        self.similarity_threshold = self.config.get("vector_similarity_threshold", 0.75)
        self.min_group_size = self.config.get("min_group_size", 3)

    def analyze(self) -> List[Dict]:
        """
        Analyze theme groups and generate signals.
        
        Returns:
            List of trade signals
        """
        signals = []

        # Get all stocks with their vectors
        stocks = self.storage.get_all_stocks()

        # For each stock, find similar stocks (theme group)
        for stock in stocks:
            similar = self.storage.find_similar_stocks(
                stock["stock_code"],
                vector_type="combined",
                top_k=10,
                threshold=self.similarity_threshold,
            )

            if len(similar) >= self.min_group_size:
                # This is a theme group leader
                group = [stock] + similar

                # Find leaders (highest momentum) and laggards
                leader = self._find_leader(group)
                laggards = self._find_laggards(group, leader["stock_code"])

                if leader and leader["stock_code"] == stock["stock_code"]:
                    # This stock is the group leader -> buy signal
                    signals.append({
                        "action": "buy",
                        "stock_code": stock["stock_code"],
                        "price": 0,  # Market order
                        "reason": f"Theme leader - similar to {len(similar)} stocks",
                        "strategy_name": "theme_trading",
                        "confidence": self._calc_confidence(leader, similar),
                    })

                    # For laggards, generate sell signals
                    for laggard in laggards[:2]:  # Max 2 sell signals
                        signals.append({
                            "action": "sell",
                            "stock_code": laggard["stock_code"],
                            "price": 0,
                            "reason": f"Theme laggard - group {stock['stock_code']}",
                            "strategy_name": "theme_trading",
                            "confidence": 0.6,
                        })

        return signals

    def _find_leader(self, group: List[Dict]) -> Dict:
        """Find the leader stock in a theme group."""
        best = None
        best_momentum = -999
        for stock in group:
            try:
                momentum = self.storage.get_latest_momentum(stock["stock_code"])
                if momentum > best_momentum:
                    best_momentum = momentum
                    best = stock
            except Exception:
                continue
        return best

    def _find_laggards(self, group: List[Dict], leader_code: str) -> List[Dict]:
        """Find laggard stocks (lowest momentum)."""
        laggards = []
        for stock in group:
            if stock["stock_code"] == leader_code:
                continue
            try:
                momentum = self.storage.get_latest_momentum(stock["stock_code"])
                if momentum < 0:
                    laggards.append((momentum, stock))
            except Exception:
                continue

        laggards.sort(key=lambda x: x[0])
        return [l[1] for l in laggards]

    def _calc_confidence(self, leader: Dict, group: List[Dict]) -> float:
        """Calculate confidence score for the signal."""
        return min(0.95, max(0.5, len(group) / 10))
