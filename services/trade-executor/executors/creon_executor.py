"""
Creon API Trade Executor
Executes buy/sell orders via Creon (Daishin) API on Windows.

Prerequisites:
- Creon API installed and configured
- Python 32-bit (Creon API requires 32-bit COM)
"""

import time
from typing import Dict, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str] = None
    order_number: Optional[str] = None
    error_code: Optional[int] = None
    error_message: Optional[str] = None
    stock_code: Optional[str] = None
    quantity: int = 0
    price: int = 0


class CreonExecutor:
    """Creon API wrapper for order execution."""

    def __init__(self):
        self._cp_trade = None
        self._cp_order = None
        self._cp_conclusion = None
        self._account = None
        self._account_type = None
        self._connected = False

    def connect(self) -> bool:
        """
        Connect to Creon API.
        Returns True if connected successfully.
        """
        try:
            import win32com.client

            # Initialize trade module
            self._cp_trade = win32com.client.Dispatch("CpTrade.CpTdUtil")
            self._cp_trade.Initialize()

            # Get account info
            account_number = self._cp_trade.AccountNumber
            if not account_number:
                logger.error("No account found in Creon API")
                return False

            self._account = account_number[0]

            # Get goods list (stock trading account type)
            goods_list = self._cp_trade.GoodsList(self._account, 1)  # 1 = stocks
            if goods_list:
                self._account_type = goods_list[0]

            # Initialize order module
            self._cp_order = win32com.client.Dispatch("CpTrade.CpTd5311")
            self._cp_conclusion = win32com.client.Dispatch("CpTrade.CpTd5339")

            self._connected = True
            logger.success(f"Connected to Creon API. Account: {self._account}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Creon API: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Disconnect from Creon API."""
        self._connected = False
        logger.info("Disconnected from Creon API")

    def buy_order(self, stock_code: str, quantity: int, price: int,
                  order_type: str = "market") -> OrderResult:
        """
        Execute buy order.
        
        Args:
            stock_code: 6-digit stock code (e.g., "005930")
            quantity: Number of shares
            price: Order price (0 for market order)
            order_type: "market" or "limit"
        
        Returns:
            OrderResult with execution details
        """
        if not self._connected and not self.connect():
            return OrderResult(success=False, error_message="Not connected to Creon")

        try:
            # Set order parameters
            self._cp_order.SetInputValue(0, "2")  # 2 = buy (매수)
            self._cp_order.SetInputValue(1, self._account)
            self._cp_order.SetInputValue(2, self._account_type)
            self._cp_order.SetInputValue(3, stock_code)
            self._cp_order.SetInputValue(4, quantity)

            if order_type == "market":
                self._cp_order.SetInputValue(5, 0)  # Price (0 for market)
                self._cp_order.SetInputValue(7, "0")  # 0 = market order (시장가)
            else:
                self._cp_order.SetInputValue(5, price)
                self._cp_order.SetInputValue(7, "1")  # 1 = limit order (지정가)

            self._cp_order.SetInputValue(8, "")  # No condition

            # Send request (blocking)
            result_code = self._cp_order.BlockRequest()

            if result_code == 0:
                order_id = self._cp_order.GetHeaderValue(8)
                order_number = self._cp_order.GetHeaderValue(9)
                logger.success(f"Buy order submitted: {stock_code} x{quantity} @ {price}")
                return OrderResult(
                    success=True,
                    order_id=str(order_id),
                    order_number=str(order_number),
                    stock_code=stock_code,
                    quantity=quantity,
                    price=price,
                )
            else:
                error_msg = self._get_error_message(result_code)
                logger.error(f"Buy order failed: {error_msg}")
                return OrderResult(
                    success=False,
                    error_code=result_code,
                    error_message=error_msg,
                )

        except Exception as e:
            logger.error(f"Exception in buy_order: {e}")
            return OrderResult(success=False, error_message=str(e))

    def sell_order(self, stock_code: str, quantity: int, price: int,
                   order_type: str = "market") -> OrderResult:
        """
        Execute sell order.
        
        Args:
            stock_code: 6-digit stock code
            quantity: Number of shares
            price: Order price (0 for market order)
            order_type: "market" or "limit"
        
        Returns:
            OrderResult with execution details
        """
        if not self._connected and not self.connect():
            return OrderResult(success=False, error_message="Not connected to Creon")

        try:
            # Set order parameters
            self._cp_order.SetInputValue(0, "1")  # 1 = sell (매도)
            self._cp_order.SetInputValue(1, self._account)
            self._cp_order.SetInputValue(2, self._account_type)
            self._cp_order.SetInputValue(3, stock_code)
            self._cp_order.SetInputValue(4, quantity)

            if order_type == "market":
                self._cp_order.SetInputValue(5, 0)
                self._cp_order.SetInputValue(7, "0")  # market order
            else:
                self._cp_order.SetInputValue(5, price)
                self._cp_order.SetInputValue(7, "1")  # limit order

            self._cp_order.SetInputValue(8, "")

            # Send request (blocking)
            result_code = self._cp_order.BlockRequest()

            if result_code == 0:
                order_id = self._cp_order.GetHeaderValue(8)
                order_number = self._cp_order.GetHeaderValue(9)
                logger.success(f"Sell order submitted: {stock_code} x{quantity} @ {price}")
                return OrderResult(
                    success=True,
                    order_id=str(order_id),
                    order_number=str(order_number),
                    stock_code=stock_code,
                    quantity=quantity,
                    price=price,
                )
            else:
                error_msg = self._get_error_message(result_code)
                logger.error(f"Sell order failed: {error_msg}")
                return OrderResult(
                    success=False,
                    error_code=result_code,
                    error_message=error_msg,
                )

        except Exception as e:
            logger.error(f"Exception in sell_order: {e}")
            return OrderResult(success=False, error_message=str(e))

    def cancel_order(self, order_id: str, stock_code: str,
                     quantity: int = 0) -> OrderResult:
        """
        Cancel an existing order.
        
        Args:
            order_id: Order ID to cancel
            stock_code: Stock code of the order
            quantity: Quantity to cancel (0 = all)
        """
        try:
            import win32com.client
            cp_cancel = win32com.client.Dispatch("CpTrade.CpTd5325")

            cp_cancel.SetInputValue(0, "0")  # Cancel order
            cp_cancel.SetInputValue(1, self._account)
            cp_cancel.SetInputValue(2, self._account_type)
            cp_cancel.SetInputValue(3, stock_code)
            cp_cancel.SetInputValue(4, order_id)
            cp_cancel.SetInputValue(5, quantity)

            result_code = cp_cancel.BlockRequest()

            if result_code == 0:
                logger.success(f"Order cancelled: {order_id}")
                return OrderResult(success=True, order_id=order_id)
            else:
                return OrderResult(
                    success=False,
                    error_code=result_code,
                    error_message=f"Cancel failed: {result_code}",
                )

        except Exception as e:
            logger.error(f"Exception in cancel_order: {e}")
            return OrderResult(success=False, error_message=str(e))

    def get_account_balance(self) -> Dict:
        """Get current account balance info."""
        try:
            import win32com.client
            cp_balance = win32com.client.Dispatch("CpTrade.CpTd6033")

            cp_balance.SetInputValue(0, self._account)
            cp_balance.SetInputValue(1, self._account_type)
            cp_balance.BlockRequest()

            total_balance = cp_balance.GetHeaderValue(1)
            withdrawable = cp_balance.GetHeaderValue(2)
            stock_value = cp_balance.GetHeaderValue(3)
            total_asset = total_balance + stock_value

            return {
                "total_balance": total_balance,
                "withdrawable": withdrawable,
                "stock_value": stock_value,
                "total_asset": total_asset,
            }

        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return {}

    def _get_error_message(self, error_code: int) -> str:
        """Convert Creon error code to message."""
        error_messages = {
            0: "Success",
            -1: "Communication error",
            -2: "System error",
            -3: "Invalid request",
            -4: "Authentication failed",
            -5: "Account not found",
            -6: "Insufficient balance",
            -7: "Invalid order",
            -8: "Market closed",
            -9: "Exceeds limit",
        }
        return error_messages.get(error_code, f"Unknown error: {error_code}")
