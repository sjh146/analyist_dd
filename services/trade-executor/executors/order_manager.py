"""
Order Manager
Manages order lifecycle: validation, execution, and tracking.
"""

from typing import Dict, Optional
from loguru import logger
from executors.creon_executor import CreonExecutor, OrderResult


class OrderManager:
    """Manages trade orders with validation and tracking."""

    def __init__(self, creon: CreonExecutor):
        self.creon = creon
        self._pending_orders: Dict[str, OrderResult] = {}
        self._completed_orders: list = []

    def execute_buy(self, stock_code: str, quantity: int, price: int,
                    order_type: str = "market") -> Optional[Dict]:
        """
        Execute a buy order with validation.
        
        Args:
            stock_code: Stock code
            quantity: Number of shares
            price: Price per share (0 for market)
            order_type: 'market' or 'limit'
        
        Returns:
            Order result dict or None if invalid
        """
        # Basic validation
        if quantity <= 0:
            logger.warning(f"Invalid buy quantity: {quantity}")
            return {"success": False, "reason": "Invalid quantity"}

        if price < 0:
            logger.warning(f"Invalid buy price: {price}")
            return {"success": False, "reason": "Invalid price"}

        # Execute order
        result = self.creon.buy_order(stock_code, quantity, price, order_type)

        result_dict = self._to_dict(result)
        result_dict["action"] = "buy"
        result_dict["stock_code"] = stock_code
        result_dict["quantity"] = quantity
        result_dict["price"] = price
        result_dict["order_type"] = order_type

        if result.success and result.order_id:
            self._pending_orders[result.order_id] = result
            result_dict["order_id"] = result.order_id

        return result_dict

    def execute_sell(self, stock_code: str, quantity: int, price: int,
                     order_type: str = "market") -> Optional[Dict]:
        """
        Execute a sell order with validation.
        
        Args:
            stock_code: Stock code
            quantity: Number of shares
            price: Price per share (0 for market)
            order_type: 'market' or 'limit'
        
        Returns:
            Order result dict or None if invalid
        """
        if quantity <= 0:
            logger.warning(f"Invalid sell quantity: {quantity}")
            return {"success": False, "reason": "Invalid quantity"}

        if price < 0:
            logger.warning(f"Invalid sell price: {price}")
            return {"success": False, "reason": "Invalid price"}

        result = self.creon.sell_order(stock_code, quantity, price, order_type)

        result_dict = self._to_dict(result)
        result_dict["action"] = "sell"
        result_dict["stock_code"] = stock_code
        result_dict["quantity"] = quantity
        result_dict["price"] = price
        result_dict["order_type"] = order_type

        if result.success and result.order_id:
            self._pending_orders[result.order_id] = result
            result_dict["order_id"] = result.order_id

        return result_dict

    def cancel_order(self, order_id: str, stock_code: str,
                     quantity: int = 0) -> Optional[Dict]:
        """Cancel a pending order."""
        result = self.creon.cancel_order(order_id, stock_code, quantity)
        result_dict = self._to_dict(result)

        if result.success and order_id in self._pending_orders:
            del self._pending_orders[order_id]

        return result_dict

    def get_pending_orders(self) -> list:
        """Get list of pending orders."""
        return [self._to_dict(o) for o in self._pending_orders.values()]

    def _to_dict(self, result: OrderResult) -> Dict:
        """Convert OrderResult to dictionary."""
        return {
            "success": result.success,
            "order_id": result.order_id,
            "order_number": result.order_number,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "stock_code": result.stock_code,
            "quantity": result.quantity,
            "price": result.price,
        }
