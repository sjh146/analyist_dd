"""Smart Order Router — routes orders to the appropriate broker executor."""

import re
from typing import Dict, Optional
from loguru import logger


class SmartOrderRouter:
    """Route orders to the right broker based on stock code pattern."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {
            "korea": "creon",
            "us": "ib",
            "crypto": "ccxt",
        }
        self._brokers: Dict[str, object] = {}

    def register_broker(self, name: str, executor_instance):
        self._brokers[name] = executor_instance
        logger.info(f"Broker '{name}' registered")

    def _detect_broker(self, stock_code: str) -> str:
        if re.match(r"^\d{6}$", stock_code):
            return self.config.get("korea", "creon")
        if re.match(r"^[A-Za-z]{1,5}-[A-Za-z]{2,4}$", stock_code):
            return self.config.get("crypto", "ccxt")
        if re.match(r"^[A-Za-z]", stock_code):
            return self.config.get("us", "ib")
        return self.config.get("us", "ib")

    def route(self, order: Dict) -> Dict:
        stock_code = order.get("stock_code", order.get("symbol", ""))
        broker_name = self._detect_broker(stock_code)
        logger.info(f"Routing {stock_code} -> broker '{broker_name}'")

        primary = self._brokers.get(broker_name)
        if primary is None:
            fallback = self._find_fallback(broker_name)
            if fallback is None:
                return {
                    "broker": None,
                    "status": "error",
                    "result": {"error": f"No broker registered for '{broker_name}' and no fallback available"},
                }
            logger.warning(f"Primary '{broker_name}' not registered, using fallback")
            broker_name = fallback
            primary = self._brokers.get(fallback)

        action = order.get("action", "buy")
        qty = order.get("quantity", order.get("qty", 0))
        price = order.get("price", 0)
        order_type = order.get("order_type", "MKT")

        try:
            if action == "buy":
                result = primary.buy_order(stock_code, qty, order_type, price)
            elif action == "sell":
                result = primary.sell_order(stock_code, qty, order_type, price)
            else:
                return {
                    "broker": broker_name,
                    "status": "error",
                    "result": {"error": f"Unknown action: {action}"},
                }
            return {"broker": broker_name, "status": "success", "result": result}
        except Exception as e:
            fallback_name = self._find_fallback(broker_name)
            if fallback_name and fallback_name in self._brokers:
                logger.warning(f"Primary '{broker_name}' failed ({e}), trying fallback '{fallback_name}'")
                fallback = self._brokers[fallback_name]
                try:
                    if action == "buy":
                        result = fallback.buy_order(stock_code, qty, order_type, price)
                    elif action == "sell":
                        result = fallback.sell_order(stock_code, qty, order_type, price)
                    else:
                        return {
                            "broker": broker_name,
                            "status": "error",
                            "result": {"error": f"Unknown action: {action}"},
                        }
                    return {"broker": fallback_name, "status": "success", "result": result}
                except Exception as fallback_e:
                    return {
                        "broker": broker_name,
                        "status": "error",
                        "result": {"error": f"Primary failed: {e}; Fallback failed: {fallback_e}"},
                    }
            return {
                "broker": broker_name,
                "status": "error",
                "result": {"error": str(e)},
            }

    def _find_fallback(self, broker_name: str) -> Optional[str]:
        fallback_map = {
            "creon": "mock",
            "mock": "creon",
            "ib": "mock",
            "ccxt": "mock",
        }
        fb = fallback_map.get(broker_name)
        if fb in self._brokers:
            return fb
        for name in self._brokers:
            if name != broker_name:
                return name
        return None

    def best_execution(self, order: Dict, brokers: list) -> Dict:
        candidates = []
        for name in brokers:
            executor = self._brokers.get(name)
            if executor is None:
                continue
            try:
                ticker = None
                symbol = order.get("stock_code", order.get("symbol", ""))
                if hasattr(executor, "get_ticker"):
                    ticker = executor.get_ticker(symbol)
                elif hasattr(executor, "get_account_summary"):
                    pass
                spread = 0.0
                commission = 0.0
                if ticker and ticker.get("ask") and ticker.get("bid"):
                    spread = abs(ticker["ask"] - ticker["bid"]) / ticker["bid"] * 100
                estimate = spread + commission
                candidates.append({
                    "broker": name,
                    "estimated_cost": estimate,
                    "spread_pct": spread,
                    "commission_pct": commission,
                })
            except Exception as e:
                logger.debug(f"best_execution skip {name}: {e}")
                continue
        if not candidates:
            return {"broker": None, "estimated_cost": float("inf")}
        candidates.sort(key=lambda c: c["estimated_cost"])
        return candidates[0]
