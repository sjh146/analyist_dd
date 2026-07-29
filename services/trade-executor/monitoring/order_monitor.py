"""
Order Monitor
Monitors order status and position P&L in real-time.
"""

import time
import threading
from typing import Dict, Optional
from datetime import datetime
from loguru import logger
from executors.creon_executor import CreonExecutor


class OrderMonitor:
    """Monitors order execution status and positions."""

    def __init__(self, creon: CreonExecutor):
        self.creon = creon
        self._last_check_time = {}
        self._running = False
        self.pending_orders: list = []
        self.positions: Dict[str, Dict] = {}

    def run(self):
        """Run monitoring loop in background thread."""
        self._running = True
        logger.info("Order monitor started")
        
        while self._running:
            try:
                self._check_pending_orders()
                self._check_positions_pnl()
                time.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                time.sleep(60)

    def stop(self):
        """Stop monitoring."""
        self._running = False

    def get_order_status(self, order_id: str) -> Optional[Dict]:
        """
        Get current status of an order.
        
        Args:
            order_id: Creon order ID
        
        Returns:
            Order status dict or None
        """
        try:
            import win32com.client
            cp_conclusion = win32com.client.Dispatch("CpTrade.CpTd5339")

            cp_conclusion.SetInputValue(0, order_id)
            cp_conclusion.BlockRequest()

            status_code = cp_conclusion.GetHeaderValue(0)
            status_map = {
                0: "pending",
                1: "partial_filled",
                2: "filled",
                3: "cancelled",
                4: "rejected",
            }

            return {
                "order_id": order_id,
                "status": status_map.get(status_code, "unknown"),
                "filled_quantity": cp_conclusion.GetHeaderValue(2),
                "filled_price": cp_conclusion.GetHeaderValue(3),
                "remaining_quantity": cp_conclusion.GetHeaderValue(4),
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to get order status: {e}")
            return None

    def _check_pending_orders(self):
        """Check status of pending orders."""
        if not self.pending_orders:
            logger.info("No pending orders to check")
            return

        for order in self.pending_orders:
            order_id = order.get("order_id")
            stock_code = order.get("stock_code")
            logger.info(f"Checking order {order_id} for {stock_code}")

            status = self.get_order_status(order_id)
            if status:
                state = status.get("status")
                if state in ("filled", "cancelled", "rejected"):
                    self.pending_orders.remove(order)
                    logger.info(f"Order {order_id} {state}, removed from pending")

    def _check_positions_pnl(self):
        """Calculate and log current P&L for all positions."""
        if not self.positions:
            logger.info("No positions to check")
            return

        for stock_code, pos in self.positions.items():
            qty = pos.get("quantity", 0)
            avg_price = pos.get("avg_price", 0)
            current_price = (
                pos.get("current_price")
                or (pos.get("current_value", 0) / qty if qty > 0 else 0)
            )

            if qty <= 0 or avg_price <= 0:
                continue

            pnl = (current_price - avg_price) * qty
            pnl_pct = ((current_price - avg_price) / avg_price) * 100

            logger.info(f"{stock_code}: unrealized PnL = {pnl:.2f} ({pnl_pct:.1f}%)")

            if pnl_pct < -10:
                logger.warning(f"Large unrealized loss on {stock_code}: {pnl_pct:.1f}%")
