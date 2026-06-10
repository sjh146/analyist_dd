"""
Position Sizer
Calculates optimal position size for each trade.
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


class PositionSizer:
    """Calculates position sizes based on risk parameters."""

    def __init__(self):
        self.default_size = 10  # Default number of shares
        self.max_size = 1000
        self.base_amount = 1000000  # 1M KRW base per trade

    def calculate(self, signal: Dict) -> int:
        """
        Calculate position size for a signal.
        
        Args:
            signal: Trade signal dict
        
        Returns:
            Number of shares to trade
        """
        confidence = signal.get("confidence", 0.5)

        # Base quantity from confidence
        base_qty = int(self.default_size * (0.5 + confidence))

        # Apply maximum position limit
        quantity = min(base_qty, self.max_size)

        logger.debug(f"Position size: {quantity} (confidence={confidence:.2f})")
        return max(1, quantity)
