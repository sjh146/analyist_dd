"""
Base Strategy Class
All trading strategies inherit from this.
"""

from typing import Dict, List, Optional
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(self, name: str, storage, config: Optional[Dict] = None):
        self.name = name
        self.storage = storage
        self.config = config or {}
        self._load_config()

    def _load_config(self):
        """Load strategy configuration from database."""
        try:
            db_config = self.storage.get_strategy_config(self.name)
            if db_config:
                self.config.update(db_config)
        except Exception as e:
            logger.debug(f"No config found for {self.name}: {e}")

    @abstractmethod
    def analyze(self) -> List[Dict]:
        """
        Run strategy analysis and generate signals.
        
        Returns:
            List of signal dicts with: action, stock_code, quantity, price, reason
        """
        pass

    def validate_signal(self, signal: Dict) -> bool:
        """Validate a single signal."""
        required = ["action", "stock_code"]
        for field in required:
            if field not in signal:
                logger.warning(f"Signal missing required field: {field}")
                return False
        return True
