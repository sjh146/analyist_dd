"""
Stop Loss Manager
Monitors positions and generates stop-loss/take-profit signals.
"""

import logging
from datetime import datetime, date
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SELL_SIGNAL = "sell"
HOLD_SIGNAL = "hold"


class StopLoss:
    """Manages stop-loss and take-profit logic."""

    def __init__(self, pg_storage=None):
        self.pg_storage = pg_storage
        self.default_stop_loss_pct = 0.07  # 7%
        self.default_take_profit_pct = 0.15  # 15%

    def evaluate_positions(self) -> List[Dict]:
        if not self.pg_storage:
            logger.warning("StopLoss: pg_storage not configured")
            return []
        positions = self.pg_storage.get_positions()
        if not positions:
            return []
        signals = []
        for pos in positions:
            stock_code = pos.get("stock_code")
            if not stock_code:
                continue
            current_price = self.pg_storage.get_latest_price(stock_code)
            if current_price is None or current_price <= 0:
                continue
            if self.check_stop_loss(pos, current_price):
                signal = self.get_stop_signal(pos, stock_code)
                signal["price"] = current_price
                signals.append(signal)
            elif self.check_take_profit(pos, current_price):
                signal = self.get_profit_signal(pos, stock_code)
                signal["price"] = current_price
                signals.append(signal)
        return signals

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
            "signal": "sell",
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
            "signal": "sell",
            "stock_code": stock_code,
            "price": 0,
            "reason": f"Take-profit triggered for {stock_code}",
            "strategy_name": "risk_management",
            "confidence": 0.9,
        }

    def trailing_stop(
        self, position: Dict, current_price: float,
        highest_price: Optional[float] = None, trail_pct: float = 0.07,
    ) -> Dict:
        highest = max(highest_price or position.get("avg_price", 0), current_price)
        stop_price = highest * (1.0 - trail_pct)
        if current_price <= stop_price:
            return {
                "action": SELL_SIGNAL,
                "signal": SELL_SIGNAL,
                "stock_code": position.get("stock_code", ""),
                "price": current_price,
                "reason": f"Trailing stop triggered at {current_price:.0f} from high {highest:.0f}",
                "strategy_name": "risk_management",
                "confidence": 1.0,
            }
        return {
            "action": HOLD_SIGNAL,
            "signal": HOLD_SIGNAL,
            "stock_code": position.get("stock_code", ""),
            "price": current_price,
            "reason": "Holding",
            "strategy_name": "risk_management",
            "confidence": 0.0,
        }

    def volatility_stop(
        self, position: Dict, current_price: float,
        atr: float, multiplier: float = 2.0,
    ) -> Dict:
        entry_price = position.get("avg_buy_price", 0)
        if entry_price <= 0:
            return {
                "action": HOLD_SIGNAL, "signal": HOLD_SIGNAL,
                "stock_code": position.get("stock_code", ""),
                "price": current_price, "reason": "No entry price",
                "strategy_name": "risk_management", "confidence": 0.0,
            }
        stop_price = entry_price - multiplier * atr
        if current_price <= stop_price:
            return {
                "action": SELL_SIGNAL,
                "signal": SELL_SIGNAL,
                "stock_code": position.get("stock_code", ""),
                "price": current_price,
                "reason": f"Volatility stop triggered at {current_price:.0f} (stop {stop_price:.0f})",
                "strategy_name": "risk_management",
                "confidence": 1.0,
            }
        return {
            "action": HOLD_SIGNAL,
            "signal": HOLD_SIGNAL,
            "stock_code": position.get("stock_code", ""),
            "price": current_price,
            "reason": "Holding",
            "strategy_name": "risk_management",
            "confidence": 0.0,
        }

    def time_stop(self, position: Dict, max_hold_days: int = 20) -> Dict:
        entry_date = position.get("entry_date")
        if entry_date:
            if isinstance(entry_date, str):
                entry_date = datetime.strptime(entry_date, "%Y-%m-%d").date()
            elif isinstance(entry_date, datetime):
                entry_date = entry_date.date()
            if (date.today() - entry_date).days >= max_hold_days:
                return {
                    "action": SELL_SIGNAL,
                    "signal": SELL_SIGNAL,
                    "stock_code": position.get("stock_code", ""),
                    "price": 0,
                    "reason": f"Time stop: held {(date.today() - entry_date).days} days >= {max_hold_days}",
                    "strategy_name": "risk_management",
                    "confidence": 1.0,
                }
        return {
            "action": HOLD_SIGNAL,
            "signal": HOLD_SIGNAL,
            "stock_code": position.get("stock_code", ""),
            "price": 0,
            "reason": "Holding",
            "strategy_name": "risk_management",
            "confidence": 0.0,
        }
