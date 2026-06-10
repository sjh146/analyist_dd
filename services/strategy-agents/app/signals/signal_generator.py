"""
Signal Generator
Creates and manages trade signals.
"""

from typing import Dict, List
from dataclasses import dataclass, field
from datetime import datetime
import uuid


@dataclass
class TradeSignal:
    """Trade signal data class."""
    action: str  # buy, sell
    stock_code: str
    quantity: int = 0
    price: float = 0.0
    order_type: str = "market"  # market, limit
    strategy_name: str = ""
    reason: str = ""
    confidence: float = 0.5
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class SignalGenerator:
    """Generates and manages trade signals."""

    def generate(self, signal_data: Dict) -> TradeSignal:
        """Create a TradeSignal from dict."""
        return TradeSignal(**signal_data)

    def combine_signals(self, signals: List[Dict]) -> List[TradeSignal]:
        """Create multiple signals."""
        return [self.generate(s) for s in signals]

    def dict_to_redis(self, signal: TradeSignal) -> Dict:
        """Convert to dict for Redis publishing."""
        return {
            "signal_id": signal.signal_id,
            "action": signal.action,
            "stock_code": signal.stock_code,
            "quantity": signal.quantity,
            "price": signal.price,
            "order_type": signal.order_type,
            "strategy_name": signal.strategy_name,
            "reason": signal.reason,
            "confidence": signal.confidence,
            "timestamp": signal.timestamp,
        }
