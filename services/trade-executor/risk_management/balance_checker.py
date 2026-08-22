"""
Balance Checker
Validates account balance before trade execution.
"""

from typing import Dict, Optional
import os
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
            # fail-closed: 잔고를 확인할 수 없으면 주문 거부 (CWE-703 — 브로커 장애 시
            # 잔고 검증 없이 매수 실행되는 경로 차단)
            logger.error("Could not check balance — rejecting order (fail-closed)")
            return False

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
            # USE_MOCK_CREON=true → mock 잔고 (Creon API 없이 paper trading)
            if os.getenv("USE_MOCK_CREON", "").lower() == "true":
                from executors.mock_creon_executor import MockCreonExecutor

                creon = MockCreonExecutor()
            else:
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
