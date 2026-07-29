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

    def calculate_kelly_fraction(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        if avg_loss <= 0:
            return 0.0
        if win_rate <= 0.0 or win_rate >= 1.0:
            return 0.0
        b = avg_win / avg_loss
        kelly = win_rate - (1.0 - win_rate) / b
        return max(0.0, min(kelly, 0.25))

    def calculate_var(self, returns: list, confidence: float = 0.95) -> float:
        if not returns or len(returns) < 10:
            return 0.02
        sorted_rets = sorted(returns)
        idx = int((1.0 - confidence) * len(sorted_rets))
        idx = max(0, min(idx, len(sorted_rets) - 1))
        return abs(sorted_rets[idx])

    def calculate_volatility_position(
        self, close_prices: list, base_risk: float = 0.02,
    ) -> int:
        if not close_prices:
            return self.default_size
        daily_ranges = []
        for item in close_prices:
            if isinstance(item, dict):
                high = item.get("high", 0)
                low = item.get("low", 0)
                close = item.get("close", 0)
            elif isinstance(item, (list, tuple)):
                if len(item) < 3:
                    continue
                high, low, close = item[1], item[2], item[3]
            else:
                continue
            if close > 0:
                daily_ranges.append((high - low) / close)
        if not daily_ranges:
            return self.default_size
        volatility = sum(daily_ranges) / len(daily_ranges)
        if volatility <= 0:
            return self.default_size
        latest_close = (
            close_prices[-1]["close"]
            if isinstance(close_prices[-1], dict) and "close" in close_prices[-1]
            else close_prices[-1]
        )
        if isinstance(latest_close, (list, tuple)):
            latest_close = latest_close[3] if len(latest_close) > 3 else latest_close[0]
        if not latest_close or latest_close <= 0:
            return self.default_size
        shares = int(base_risk * self.base_amount / (volatility * latest_close))
        return max(1, min(shares, self.max_size))
