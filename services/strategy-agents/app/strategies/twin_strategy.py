"""
Twin Trading Strategy
Pairs trading on highly correlated stocks (TWIN_OF in Neo4j).
"""

import numpy as np
import logging
from typing import List, Dict
from app.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class TwinStrategy(BaseStrategy):
    def __init__(self, storage):
        super().__init__("twin_trading", storage)
        self.min_correlation = self.config.get("min_correlation", 0.80)
        self.zscore_entry = self.config.get("zscore_entry", 2.0)
        self.zscore_exit = self.config.get("zscore_exit", 0.5)

    def analyze(self) -> List[Dict]:
        """
        Find twin stock pairs with price divergence and generate signals.
        
        Returns:
            List of trade signals for pairs
        """
        signals = []

        # Get twin pairs from Neo4j
        twin_pairs = self.storage.get_twin_pairs(
            min_correlation=self.min_correlation
        )

        for pair in twin_pairs:
            stock_a = pair["stock_code_a"]
            stock_b = pair["stock_code_b"]
            correlation = pair.get("correlation", 0.8)

            # Calculate spread and z-score
            spread_data = self._calculate_spread(stock_a, stock_b)

            if not spread_data:
                continue

            zscore = spread_data["zscore"]
            current_spread = spread_data["spread"]

            if abs(zscore) > self.zscore_entry:
                # Divergence detected -> trade the convergence
                if zscore > 0:
                    # Stock A is overpriced relative to stock B
                    signals.append({
                        "action": "sell",
                        "stock_code": stock_a,
                        "price": 0,
                        "reason": f"Twin divergence: z-score={zscore:.2f}, short {stock_a}",
                        "strategy_name": "twin_trading",
                        "confidence": min(0.9, abs(zscore) / 3),
                    })
                    signals.append({
                        "action": "buy",
                        "stock_code": stock_b,
                        "price": 0,
                        "reason": f"Twin divergence: z-score={zscore:.2f}, long {stock_b}",
                        "strategy_name": "twin_trading",
                        "confidence": min(0.9, abs(zscore) / 3),
                    })
                else:
                    # Stock A is underpriced relative to stock B
                    signals.append({
                        "action": "buy",
                        "stock_code": stock_a,
                        "price": 0,
                        "reason": f"Twin divergence: z-score={zscore:.2f}, long {stock_a}",
                        "strategy_name": "twin_trading",
                        "confidence": min(0.9, abs(zscore) / 3),
                    })
                    signals.append({
                        "action": "sell",
                        "stock_code": stock_b,
                        "price": 0,
                        "reason": f"Twin divergence: z-score={zscore:.2f}, short {stock_b}",
                        "strategy_name": "twin_trading",
                        "confidence": min(0.9, abs(zscore) / 3),
                    })

            elif abs(zscore) < self.zscore_exit and abs(zscore) > 0:
                # Convergence, close positions
                pass  # Position management done by trade executor

        return signals

    def _calculate_spread(self, stock_a: str, stock_b: str) -> Dict:
        """Calculate price spread and z-score between two stocks."""
        try:
            prices_a = self.storage.get_price_series(stock_a, days=60)
            prices_b = self.storage.get_price_series(stock_b, days=60)

            if not prices_a or not prices_b:
                return {}

            # Normalize prices
            prices_a = np.array(prices_a)
            prices_b = np.array(prices_b)
            prices_a = prices_a / prices_a[0]
            prices_b = prices_b / prices_b[0]

            # Calculate spread
            spread = prices_a - prices_b
            mean_spread = np.mean(spread)
            std_spread = np.std(spread)

            if std_spread == 0:
                return {}

            current_zscore = (spread[-1] - mean_spread) / std_spread

            return {
                "spread": float(spread[-1]),
                "mean": float(mean_spread),
                "std": float(std_spread),
                "zscore": float(current_zscore),
            }
        except Exception as e:
            logger.debug(f"Failed to calculate spread: {e}")
            return {}
