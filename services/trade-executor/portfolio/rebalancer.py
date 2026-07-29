import math
from typing import List, Dict, Any, Optional


class RebalancingEngine:
    """Rebalancing engine for portfolio weight management."""

    MAX_SINGLE_WEIGHT = 0.3

    def calculate_target_weights(
        self,
        strategy_signals: List[Dict[str, Any]],
        risk_budget: float = 1.0,
    ) -> Dict[str, float]:
        if not strategy_signals:
            return {}

        total_confidence = sum(s.get("confidence", 0) for s in strategy_signals)

        if total_confidence == 0:
            equal = risk_budget / len(strategy_signals)
            return {s["stock_code"]: equal for s in strategy_signals}

        targets: Dict[str, float] = {}
        for s in strategy_signals:
            raw = s["confidence"] / total_confidence * risk_budget
            targets[s["stock_code"]] = min(raw, self.MAX_SINGLE_WEIGHT)
        return targets

    def detect_drift(
        self,
        current_weights: Dict[str, float],
        target_weights: Dict[str, float],
        threshold: float = 0.05,
    ) -> List[Dict[str, Any]]:
        all_stocks = set(current_weights.keys()) | set(target_weights.keys())
        signals: List[Dict[str, Any]] = []

        for stock in sorted(all_stocks):
            cw = current_weights.get(stock, 0.0)
            tw = target_weights.get(stock, 0.0)

            if tw == 0:
                if cw > 0:
                    signals.append(
                        {
                            "stock_code": stock,
                            "current_weight": cw,
                            "target_weight": tw,
                            "drift_pct": 1.0,
                            "action": "sell",
                        }
                    )
                continue

            drift_pct = abs(cw - tw) / tw
            if drift_pct > threshold:
                action = "buy" if cw < tw else "sell"
                signals.append(
                    {
                        "stock_code": stock,
                        "current_weight": cw,
                        "target_weight": tw,
                        "drift_pct": round(drift_pct, 6),
                        "action": action,
                    }
                )

        return signals

    def generate_rebalance_orders(
        self,
        drift_signals: List[Dict[str, Any]],
        portfolio_value: float,
        current_prices: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        orders: List[Dict[str, Any]] = []

        for sig in drift_signals:
            if sig["action"] == "hold":
                continue

            weight_diff = abs(sig["current_weight"] - sig["target_weight"])
            dollar_amount = weight_diff * portfolio_value
            stock_code = sig["stock_code"]
            action = sig["action"]

            quantity: Optional[float] = None
            if current_prices and stock_code in current_prices:
                price = current_prices[stock_code]
                if price > 0:
                    quantity = math.floor(dollar_amount / price)

            orders.append(
                {
                    "stock_code": stock_code,
                    "action": action,
                    "quantity": quantity,
                    "dollar_amount": round(dollar_amount, 2),
                    "reason": (
                        f"Drift {sig['drift_pct']:.2%} exceeds threshold"
                    ),
                }
            )

        return orders
