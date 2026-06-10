"""
Stop Loss Manager
Monitors positions and generates stop-loss/take-profit signals.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class StopLoss:
    """Manages stop-loss and take-profit logic."""

    def __init__(self):
        self.default_stop_loss_pct = 0.07  # 7%
        self.default_take_profit_pct = 0.15  # 15%

    def check_stop_loss(self, position: Dict, current_price: float) -> bool:
        """Check if stop-loss should trigger."""
        if not position or not current_price:
            return False
        avg_price = position.get("avg_buy_price", 0)
        if avg_price <= 0:
            return False
        loss_pct = (current_price - avg_price) / avg_price
        sl_pct = position.get("stop_loss_pct", self.default_stop_loss_pct)
        return loss_pct <= -sl_pct

    def check_take_profit(self, position: Dict, current_price: float) -> bool:
        """Check if take-profit should trigger."""
        if not position or not current_price:
            return False
        avg_price = position.get("avg_buy_price", 0)
        if avg_price <= 0:
            return False
        gain_pct = (current_price - avg_price) / avg_price
        tp_pct = position.get("take_profit_pct", self.default_take_profit_pct)
        return gain_pct >= tp_pct

    def get_stop_signal(self, position: Dict, stock_code: str) -> Dict:
        """Generate stop-loss sell signal."""
        return {
            "action": "sell",
            "stock_code": stock_code,
            "price": 0,
            "reason": f"Stop-loss triggered for {stock_code}",
            "strategy_name": "risk_management",
            "confidence": 1.0,
        }

    def get_profit_signal(self, position: Dict, stock_code: str) -> Dict:
        """Generate take-profit sell signal."""
        return {
            "action": "sell",
            "stock_code": stock_code,
            "price": 0,
            "reason": f"Take-profit triggered for {stock_code}",
            "strategy_name": "risk_management",
            "confidence": 0.9,
        }
