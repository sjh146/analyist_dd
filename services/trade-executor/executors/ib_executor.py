"""IBKR TWS/Gateway executor using ib_insync."""

import time
from typing import Dict, Optional
from loguru import logger


class IBExecutor:
    """Execute orders via Interactive Brokers TWS/Gateway API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib = None
        self._connected = False

    def connect(self) -> bool:
        try:
            from ib_insync import IB

            self._ib = IB()
            self._ib.connect(self.host, self.port, clientId=self.client_id)
            self._connected = True
            logger.success(f"IB connected to {self.host}:{self.port}")
            return True
        except ImportError:
            logger.warning("ib_insync not installed; pip install ib_insync")
            raise RuntimeError("ib_insync is not installed")
        except Exception as e:
            self._connected = False
            raise RuntimeError(f"IB connection failed: {e}")

    def disconnect(self):
        if self._ib and self._connected:
            try:
                self._ib.disconnect()
            except Exception as e:
                logger.error(f"IB disconnect error: {e}")
        self._connected = False
        logger.info("IB disconnected")

    def is_connected(self) -> bool:
        if self._ib is None:
            return False
        try:
            return self._ib.isConnected()
        except Exception:
            return False

    def buy_order(
        self,
        symbol: str,
        qty: int,
        order_type: str = "MKT",
        price: float = 0,
        tif: str = "DAY",
    ) -> Dict:
        if not self.is_connected():
            raise RuntimeError("Not connected to IB")
        try:
            from ib_insync import Stock, MarketOrder, LimitOrder, Action

            contract = Stock(symbol, "SMART", "USD")
            ib_contract = self._ib.qualifyContracts(contract)
            if not ib_contract:
                raise RuntimeError(f"Could not qualify contract {symbol}")
            order_type_upper = order_type.upper()
            if order_type_upper == "MKT":
                ib_order = MarketOrder(Action.BUY, qty, tif=tif)
            elif order_type_upper == "LMT":
                ib_order = LimitOrder(Action.BUY, price, qty, tif=tif)
            else:
                raise RuntimeError(f"Unsupported order type: {order_type}")
            trade = self._ib.placeOrder(ib_contract[0], ib_order)
            self._ib.sleep(0.5)
            log = trade.log
            status = trade.orderStatus.status if trade.orderStatus else "unknown"
            return {
                "order_id": str(trade.order.orderId),
                "status": status,
                "filled_qty": trade.orderStatus.filled if trade.orderStatus else 0,
                "avg_price": trade.orderStatus.avgFillPrice if trade.orderStatus else 0.0,
                "symbol": symbol,
                "action": "buy",
            }
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"IB buy_order failed: {e}")

    def sell_order(
        self,
        symbol: str,
        qty: int,
        order_type: str = "MKT",
        price: float = 0,
        tif: str = "DAY",
    ) -> Dict:
        if not self.is_connected():
            raise RuntimeError("Not connected to IB")
        try:
            from ib_insync import Stock, MarketOrder, LimitOrder, Action

            contract = Stock(symbol, "SMART", "USD")
            ib_contract = self._ib.qualifyContracts(contract)
            if not ib_contract:
                raise RuntimeError(f"Could not qualify contract {symbol}")
            order_type_upper = order_type.upper()
            if order_type_upper == "MKT":
                ib_order = MarketOrder(Action.SELL, qty, tif=tif)
            elif order_type_upper == "LMT":
                ib_order = LimitOrder(Action.SELL, price, qty, tif=tif)
            else:
                raise RuntimeError(f"Unsupported order type: {order_type}")
            trade = self._ib.placeOrder(ib_contract[0], ib_order)
            self._ib.sleep(0.5)
            return {
                "order_id": str(trade.order.orderId),
                "status": trade.orderStatus.status if trade.orderStatus else "unknown",
                "filled_qty": trade.orderStatus.filled if trade.orderStatus else 0,
                "avg_price": trade.orderStatus.avgFillPrice if trade.orderStatus else 0.0,
                "symbol": symbol,
                "action": "sell",
            }
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"IB sell_order failed: {e}")

    def cancel_order(self, order_id: str) -> bool:
        if not self.is_connected():
            raise RuntimeError("Not connected to IB")
        try:
            orders = self._ib.orders()
            for o in orders:
                if str(o.orderId) == order_id:
                    self._ib.cancelOrder(o)
                    logger.info(f"IB order {order_id} cancelled")
                    return True
            logger.warning(f"IB order {order_id} not found")
            return False
        except Exception as e:
            raise RuntimeError(f"IB cancel_order failed: {e}")

    def get_positions(self) -> list:
        if not self.is_connected():
            raise RuntimeError("Not connected to IB")
        try:
            positions = self._ib.positions()
            result = []
            for p in positions:
                result.append({
                    "symbol": p.contract.symbol,
                    "position": p.position,
                    "avg_cost": p.avgCost,
                    "market_price": p.marketPrice,
                    "pnl": p.marketValue - p.avgCost * p.position if p.position else 0,
                })
            return result
        except Exception as e:
            raise RuntimeError(f"IB get_positions failed: {e}")

    def get_account_summary(self) -> Dict:
        if not self.is_connected():
            raise RuntimeError("Not connected to IB")
        try:
            summary = self._ib.accountSummary()
            values = {item.tag: item.value for item in summary}
            return {
                "total_cash": float(values.get("TotalCashValue", 0)),
                "buying_power": float(values.get("BuyingPower", 0)),
                "gross_position_value": float(values.get("GrossPositionValue", 0)),
                "net_liquidation": float(values.get("NetLiquidation", 0)),
            }
        except Exception as e:
            raise RuntimeError(f"IB get_account_summary failed: {e}")
