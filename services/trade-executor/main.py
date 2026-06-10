"""
Trade Executor Service (Windows VM)
- Listens for trade signals from Redis (sent by Strategy Agents on Linux)
- Executes trades via Creon API
- Reports order status back to Redis
"""

import time
import json
import threading
from datetime import datetime, time as dtime
from typing import Dict, Optional
from loguru import logger
from config import Config

from utils.redis_client import RedisClient
from executors.creon_executor import CreonExecutor
from executors.order_manager import OrderManager
from risk_management.position_checker import PositionChecker
from risk_management.balance_checker import BalanceChecker
from monitoring.order_monitor import OrderMonitor


class TradeExecutor:
    """Main trade executor that orchestrates all components."""

    def __init__(self):
        logger.info("Initializing Trade Executor...")
        self.config = Config()
        self.redis = RedisClient(
            host=self.config.REDIS_HOST,
            port=self.config.REDIS_PORT,
            password=self.config.REDIS_PASSWORD,
        )
        self.creon = CreonExecutor()
        self.order_manager = OrderManager(self.creon)
        self.position_checker = PositionChecker()
        self.balance_checker = BalanceChecker()
        self.order_monitor = OrderMonitor(self.creon)
        self._running = False
        self._daily_trade_amount = 0
        self._reset_day = datetime.now().date()

    def _reset_daily_limits(self):
        """Reset daily trade limits at market open."""
        today = datetime.now().date()
        if self._reset_day != today:
            self._daily_trade_amount = 0
            self._reset_day = today
            logger.info("Daily trade limits reset.")

    def _check_market_hours(self) -> bool:
        """Check if market is currently open (KST 09:00 ~ 15:30)."""
        now = datetime.now()
        market_open = now.replace(
            hour=self.config.TRADING_START_HOUR, minute=0, second=0, microsecond=0
        )
        market_close = now.replace(
            hour=self.config.TRADING_END_HOUR, minute=30, second=0, microsecond=0
        )
        return market_open <= now <= market_close

    def _check_daily_limit(self, amount: int) -> bool:
        """Check if daily trade limit is exceeded."""
        return self._daily_trade_amount + amount <= self.config.MAX_DAILY_TRADE

    def process_signal(self, signal: Dict) -> Optional[Dict]:
        """
        Process a trade signal from Redis.
        Returns order result dict or None if rejected.
        """
        logger.info(f"Processing signal: {json.dumps(signal, ensure_ascii=False)}")

        # 1. Reset daily limits if needed
        self._reset_daily_limits()

        # 2. Validate market hours
        if not self._check_market_hours():
            logger.warning("Market is closed. Rejecting signal.")
            return self._reject_signal(signal, "Market closed")

        # 3. Validate balance
        action = signal.get("action", "").lower()
        amount = signal.get("quantity", 0) * signal.get("price", 0)

        if action == "buy":
            balance_ok = self.balance_checker.check_buyable(amount)
            if not balance_ok:
                return self._reject_signal(signal, "Insufficient balance")

        # 4. Check daily limit
        if not self._check_daily_limit(amount):
            return self._reject_signal(signal, "Daily trade limit exceeded")

        # 5. Check position limits
        stock_code = signal.get("stock_code", "")
        position_ok = self.position_checker.check_position_limit(
            stock_code, signal.get("quantity", 0)
        )
        if not position_ok:
            return self._reject_signal(signal, "Position limit exceeded")

        # 6. Execute order via Creon API
        if action == "buy":
            result = self.order_manager.execute_buy(
                stock_code=stock_code,
                quantity=signal["quantity"],
                price=signal.get("price", 0),
                order_type=signal.get("order_type", "market"),
            )
        elif action == "sell":
            result = self.order_manager.execute_sell(
                stock_code=stock_code,
                quantity=signal["quantity"],
                price=signal.get("price", 0),
                order_type=signal.get("order_type", "market"),
            )
        else:
            return self._reject_signal(signal, f"Unknown action: {action}")

        # 7. Update daily trade amount
        self._daily_trade_amount += amount

        # 8. Publish order result to Redis
        if result:
            result["strategy_name"] = signal.get("strategy_name", "")
            result["signal_id"] = signal.get("signal_id", "")
            self.redis.publish(self.config.ORDER_CHANNEL, result)

        # 9. Update position in PostgreSQL
        if result and result.get("success"):
            self.position_checker.update_position(stock_code, action, signal["quantity"])

        logger.info(f"Order result: {json.dumps(result, ensure_ascii=False)}")
        return result

    def _reject_signal(self, signal: Dict, reason: str) -> Dict:
        """Create rejection response."""
        result = {
            "success": False,
            "signal_id": signal.get("signal_id", ""),
            "stock_code": signal.get("stock_code", ""),
            "action": signal.get("action", ""),
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        }
        self.redis.publish(self.config.ORDER_CHANNEL, result)
        return result

    def on_signal_received(self, signal: Dict):
        """Callback when a trade signal is received from Redis."""
        try:
            result = self.process_signal(signal)
            if result and result.get("success"):
                logger.success(f"Trade executed successfully: {result}")
            else:
                logger.warning(f"Trade rejected: {result}")
        except Exception as e:
            logger.error(f"Error processing signal: {e}")

    def run(self):
        """Main loop - listen for signals and process."""
        logger.info("Starting Trade Executor...")
        logger.info(f"Connecting to Redis at {self.config.REDIS_HOST}:{self.config.REDIS_PORT}")

        # Start order monitor in background thread
        monitor_thread = threading.Thread(target=self.order_monitor.run, daemon=True)
        monitor_thread.start()

        # Subscribe to trade signals
        self.redis.subscribe(self.config.SIGNAL_CHANNEL, self.on_signal_received)

        # Keep the main thread alive
        try:
            while True:
                time.sleep(1)
                # Periodically check positions for stop-loss
                self._check_positions_periodically()
        except KeyboardInterrupt:
            logger.info("Shutting down Trade Executor...")
            self._running = False

    def _check_positions_periodically(self):
        """Check positions every 60 seconds for stop-loss/take-profit."""
        # This would be a periodic check, but let the order_monitor handle it
        pass


if __name__ == "__main__":
    executor = TradeExecutor()
    executor.run()
