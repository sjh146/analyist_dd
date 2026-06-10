"""
Signal Validator
Validates trade signals before publishing.
"""

import logging
from typing import Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class SignalValidator:
    """Validates trade signals."""

    def __init__(self):
        self._recent_signals = set()

    def validate(self, signal: Dict) -> bool:
        """
        Validate a trade signal.
        
        Returns:
            True if signal is valid
        """
        # Check required fields
        required = ["action", "stock_code", "strategy_name"]
        for field in required:
            if field not in signal:
                logger.warning(f"Missing required field: {field}")
                return False

        # Check action is valid
        if signal["action"] not in ("buy", "sell"):
            logger.warning(f"Invalid action: {signal['action']}")
            return False

        # Check stock code is valid (6 digits)
        stock_code = signal["stock_code"]
        if not stock_code or not stock_code.isdigit() or len(stock_code) != 6:
            logger.warning(f"Invalid stock code: {stock_code}")
            return False

        # Check for duplicate signals
        signal_key = f"{signal['stock_code']}_{signal['action']}_{signal.get('strategy_name', '')}"
        if signal_key in self._recent_signals:
            logger.debug(f"Duplicate signal: {signal_key}")
            return False

        # Keep track of recent signals (cleanup old ones)
        self._recent_signals.add(signal_key)
        if len(self._recent_signals) > 100:
            self._recent_signals.clear()

        return True
