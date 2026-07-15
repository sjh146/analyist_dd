"""Mock CreonExecutor for Ubuntu/CI environments where Creon API is unavailable."""

from typing import Dict, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class OrderResult:
    """Mock order result matching the real OrderResult interface."""
    success: bool
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    error_code: Optional[int] = None
    error_message: Optional[str] = None
    stock_code: Optional[str] = None
    quantity: int = 0
    price: int = 0


class MockCreonExecutor:
    """Mock executor that simulates Creon API responses without actual brokerage connection.

    Activated via environment variable: USE_MOCK_CREON=true
    """

    def __init__(self):
        self._connected = False
        self._balance = {
            "total_balance": 10000000,   # 10M KRW
            "withdrawable": 5000000,     # 5M KRW
            "stock_value": 5000000,      # 5M KRW in stocks
            "total_asset": 15000000,     # 15M KRW total
        }

    def connect(self) -> bool:
        """Simulate successful connection to Creon."""
        self._connected = True
        logger.success("MockCreonExecutor: Connected (simulated)")
        return True

    def disconnect(self) -> None:
        """Simulate disconnection."""
        self._connected = False
        logger.info("MockCreonExecutor: Disconnected (simulated)")

    def buy_order(self, stock_code: str, quantity: int, price: int,
                  order_type: str = "market") -> OrderResult:
        """Simulate a buy order. Always succeeds."""
        order_id = f"MOCK-BUY-{stock_code}-{quantity}"
        order_number = f"MN-{order_id}"
        logger.success(f"MockCreonExecutor: Buy order submitted: {stock_code} x{quantity} @ {price}")
        return OrderResult(
            success=True,
            order_id=order_id,
            order_number=order_number,
            stock_code=stock_code,
            quantity=quantity,
            price=price,
        )

    def sell_order(self, stock_code: str, quantity: int, price: int,
                   order_type: str = "market") -> OrderResult:
        """Simulate a sell order. Always succeeds."""
        order_id = f"MOCK-SELL-{stock_code}-{quantity}"
        order_number = f"MN-{order_id}"
        logger.success(f"MockCreonExecutor: Sell order submitted: {stock_code} x{quantity} @ {price}")
        return OrderResult(
            success=True,
            order_id=order_id,
            order_number=order_number,
            stock_code=stock_code,
            quantity=quantity,
            price=price,
        )

    def cancel_order(self, order_id: str, stock_code: str,
                     quantity: int = 0) -> OrderResult:
        """Simulate order cancellation. Always succeeds."""
        logger.success(f"MockCreonExecutor: Order cancelled: {order_id}")
        return OrderResult(
            success=True,
            order_id=order_id,
            stock_code=stock_code,
            quantity=quantity,
        )

    def get_account_balance(self) -> Dict:
        """Return mock account balance."""
        return dict(self._balance)
