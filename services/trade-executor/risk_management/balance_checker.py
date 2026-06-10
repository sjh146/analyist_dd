"""
Balance Checker
Validates account balance before trade execution.
"""

from typing import Dict, Optional
from loguru import logger
from config import Config


class BalanceChecker:
    """Checks account balance and trade affordability."""

    def __init__(self):
        self.config = Config()
        self._balance_cache: Optional[Dict] = None
        self._cache_time = 0

    def check_buyable(self, amount: int) -> bool:
        """
        Check if account has enough balance for a buy order.
        
        Args:
            amount: Total order amount (quantity * price)
        
        Returns:
            True if sufficient balance
        """
        balance = self.get_balance()
        if not balance:
            # If we can't get balance, default to True (allow)
            logger.warning("Could not check balance, defaulting to allowed")
            return True

        withdrawable = balance.get("withdrawable", 0)
        max_position = self.config.MAX_POSITION_SIZE

        if amount > max_position:
            logger.warning(f"Order amount {amount} exceeds max position {max_position}")
            return False

        if amount > withdrawable:
            logger.warning(f"Insufficient balance: need {amount}, have {withdrawable}")
            return False

        return True

    def get_balance(self) -> Optional[Dict]:
        """
        Get account balance from Creon API.
        Returns cached value if recent.
        """
        import time

        # Cache for 30 seconds
        if self._balance_cache and (time.time() - self._cache_time) < 30:
            return self._balance_cache

        try:
            from executors.creon_executor import CreonExecutor

            creon = CreonExecutor()
            if not creon.connect():
                return None

            balance = creon.get_account_balance()
            creon.disconnect()

            self._balance_cache = balance
            self._cache_time = time.time()

            logger.info(f"Account balance: {balance}")
            return balance

        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None
