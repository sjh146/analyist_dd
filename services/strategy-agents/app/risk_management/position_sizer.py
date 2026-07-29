"""
Position Sizer
Calculates optimal position size for each trade.
"""

import math
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class PositionSizer:
    """Calculates position sizes based on risk parameters."""

    def __init__(self):
        self.default_size = 10  # Default number of shares
        self.max_size = 1000
        self.base_amount = 1000000  # 1M KRW base per trade

    def calculate(
        self,
        signal: Dict,
        current_price: Optional[float] = None,
        account_balance: Optional[float] = None,
        max_position_size: float = 10000000,
    ) -> int:
        """
        Calculate position size for a signal.
        
        Args:
            signal: Trade signal dict
            current_price: Current stock price (KRW)
            account_balance: Available account balance (KRW)
            max_position_size: Max KRW per position (default 10M)
        
        Returns:
            Number of shares to trade
        """
        confidence = signal.get("confidence", 0.5)

        # Base quantity from confidence
        base_qty = int(self.default_size * (0.5 + confidence))

        if current_price is not None and account_balance is not None:
            max_shares_by_balance = int(account_balance * 0.3 / current_price)
            max_shares_by_fixed = int(max_position_size / current_price)
            quantity = min(max_shares_by_balance, max_shares_by_fixed, base_qty)
        else:
            quantity = min(base_qty, self.max_size)

        logger.debug(f"Position size: {quantity} (confidence={confidence:.2f})")
        return max(1, quantity)
