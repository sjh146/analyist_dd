"""CCXT-based crypto exchange executor."""

from typing import Dict, Optional
from loguru import logger


class CCXTExecutor:
    """Execute orders on crypto exchanges via the CCXT library."""

    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str = "",
        api_secret: str = "",
        config: Optional[Dict] = None,
    ):
        self.exchange_id = exchange_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.config = config or {}
        self._exchange = None

    def connect(self):
        try:
            import ccxt

            exchange_class = getattr(ccxt, self.exchange_id)
            self._exchange = exchange_class({
                "apiKey": self.api_key,
                "secret": self.api_secret,
                **self.config,
            })
            logger.success(f"CCXT connected to {self.exchange_id}")
        except ImportError:
            logger.warning("ccxt not installed; pip install ccxt")
            raise RuntimeError("ccxt is not installed")
        except AttributeError:
            raise RuntimeError(f"Unknown exchange: {self.exchange_id}")
        except Exception as e:
            raise RuntimeError(f"CCXT connection failed: {e}")

    def _ensure_connected(self):
        if self._exchange is None:
            self.connect()

    def buy_order(
        self, symbol: str, qty: float, order_type: str = "market", price: float = 0
    ) -> Dict:
        self._ensure_connected()
        try:
            params = {}
            if order_type.lower() == "limit" and price > 0:
                params["price"] = price
            order = self._exchange.create_order(
                symbol=symbol,
                type=order_type.lower(),
                side="buy",
                amount=qty,
                price=price if order_type.lower() == "limit" else None,
            )
            return {
                "order_id": str(order.get("id", "")),
                "status": order.get("status", "unknown"),
                "filled_qty": float(order.get("filled", 0)),
                "avg_price": float(order.get("average", 0)),
                "symbol": symbol,
                "action": "buy",
            }
        except Exception as e:
            raise RuntimeError(f"CCXT buy_order failed: {e}")

    def sell_order(
        self, symbol: str, qty: float, order_type: str = "market", price: float = 0
    ) -> Dict:
        self._ensure_connected()
        try:
            order = self._exchange.create_order(
                symbol=symbol,
                type=order_type.lower(),
                side="sell",
                amount=qty,
                price=price if order_type.lower() == "limit" else None,
            )
            return {
                "order_id": str(order.get("id", "")),
                "status": order.get("status", "unknown"),
                "filled_qty": float(order.get("filled", 0)),
                "avg_price": float(order.get("average", 0)),
                "symbol": symbol,
                "action": "sell",
            }
        except Exception as e:
            raise RuntimeError(f"CCXT sell_order failed: {e}")

    def cancel_order(self, order_id: str) -> bool:
        self._ensure_connected()
        try:
            self._exchange.cancel_order(order_id)
            logger.info(f"CCXT order {order_id} cancelled")
            return True
        except Exception as e:
            logger.error(f"CCXT cancel_order failed: {e}")
            return False

    def get_balance(self) -> Dict:
        self._ensure_connected()
        try:
            balance = self._exchange.fetch_balance()
            result = {}
            for currency, data in balance.get("total", {}).items():
                result[currency] = {
                    "free": balance.get("free", {}).get(currency, 0),
                    "used": balance.get("used", {}).get(currency, 0),
                    "total": data,
                }
            return result
        except Exception as e:
            raise RuntimeError(f"CCXT get_balance failed: {e}")

    def get_ticker(self, symbol: str) -> Dict:
        self._ensure_connected()
        try:
            ticker = self._exchange.fetch_ticker(symbol)
            return {
                "bid": ticker.get("bid"),
                "ask": ticker.get("ask"),
                "last": ticker.get("last"),
                "volume": ticker.get("baseVolume"),
            }
        except Exception as e:
            raise RuntimeError(f"CCXT get_ticker failed: {e}")

    def get_positions(self) -> list:
        self._ensure_connected()
        try:
            positions = self._exchange.fetch_positions()
            result = []
            for p in positions:
                result.append({
                    "symbol": p.get("symbol"),
                    "position": float(p.get("contracts", 0)),
                    "entry_price": float(p.get("entryPrice", 0)),
                    "mark_price": float(p.get("markPrice", 0)),
                    "pnl": float(p.get("unrealizedPnl", 0)),
                })
            return result
        except Exception as e:
            raise RuntimeError(f"CCXT get_positions failed: {e}")
