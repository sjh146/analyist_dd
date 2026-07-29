import math
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional


class PortfolioTracker:
    """Tracks portfolio P&L, returns, and risk metrics."""

    def calculate_pnl(
        self, positions: List[Dict[str, Any]], prices_dict: Dict[str, float]
    ) -> Dict[str, float]:
        unrealized_pnl = 0.0
        realized_pnl = 0.0
        for pos in positions:
            stock_code = pos.get("stock_code", "")
            qty = pos.get("quantity", 0)
            avg_price = pos.get("avg_buy_price", 0)
            current_price = prices_dict.get(stock_code, 0)
            unrealized_pnl += (current_price - avg_price) * qty
            realized_pnl += pos.get("realized_pnl", 0)
        return {
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "total_pnl": unrealized_pnl + realized_pnl,
        }

    def calculate_returns(
        self, portfolio_history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not portfolio_history:
            return {"daily_returns": [], "cumulative_return": 0.0, "cagr": 0.0}

        values = [h["total_value"] for h in portfolio_history]
        daily_returns = []
        for i in range(1, len(values)):
            prev = values[i - 1]
            if prev == 0:
                daily_returns.append(0.0)
            else:
                daily_returns.append(values[i] / prev - 1)

        if len(values) < 2 or values[0] == 0:
            cum_ret = 0.0
            cagr = 0.0
        else:
            cum_ret = values[-1] / values[0] - 1
            n_days = len(values) - 1
            cagr = (values[-1] / values[0]) ** (252.0 / n_days) - 1 if n_days > 0 else 0.0

        return {
            "daily_returns": daily_returns,
            "cumulative_return": cum_ret,
            "cagr": cagr,
        }

    def calculate_metrics(self, returns_series: List[float]) -> Dict[str, float]:
        if not returns_series:
            return {
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "calmar_ratio": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "volatility": 0.0,
            }

        n = len(returns_series)
        mean_ret = sum(returns_series) / n
        variance = sum((r - mean_ret) ** 2 for r in returns_series) / n
        std_dev = math.sqrt(variance) if variance > 0 else 0.0
        vol = std_dev * math.sqrt(252)

        sharpe = (mean_ret / std_dev * math.sqrt(252)) if std_dev > 0 else 0.0

        downside = [r for r in returns_series if r < 0]
        downside_var = sum(r * r for r in downside) / n if downside else 0.0
        downside_std = math.sqrt(downside_var) if downside_var > 0 else 0.0
        sortino = (mean_ret / downside_std * math.sqrt(252)) if downside_std > 0 else 0.0

        running_max = float("-inf")
        max_dd = 0.0
        value = 1.0
        for r in returns_series:
            value *= 1 + r
            if value > running_max:
                running_max = value
            if running_max > 0:
                dd = (running_max - value) / running_max
                if dd > max_dd:
                    max_dd = dd

        cum_factor = 1.0
        for r in returns_series:
            cum_factor *= 1 + r
        cagr_val = (cum_factor ** (252.0 / n) - 1) if n > 0 else 0.0
        calmar = cagr_val / max_dd if max_dd > 0 else 0.0

        win_count = sum(1 for r in returns_series if r > 0)
        win_rate = win_count / n if n > 0 else 0.0

        pos_sum = sum(r for r in returns_series if r > 0)
        neg_sum = sum(r for r in returns_series if r < 0)
        if neg_sum != 0:
            profit_factor = pos_sum / abs(neg_sum)
        else:
            profit_factor = float("inf") if pos_sum > 0 else 0.0

        return {
            "sharpe_ratio": round(sharpe, 6),
            "sortino_ratio": round(sortino, 6),
            "calmar_ratio": round(calmar, 6),
            "max_drawdown": round(max_dd, 6),
            "win_rate": round(win_rate, 6),
            "profit_factor": round(profit_factor, 6),
            "volatility": round(vol, 6),
        }

    def snapshot(
        self,
        positions: List[Dict[str, Any]],
        prices_dict: Dict[str, float],
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        pnl = self.calculate_pnl(positions, prices_dict)
        total_invested = 0.0
        cash_balance = 0.0
        positions_detail = []

        for pos in positions:
            stock_code = pos.get("stock_code", "")
            qty = pos.get("quantity", 0)
            avg_price = pos.get("avg_buy_price", 0)
            current_price = prices_dict.get(stock_code, 0)
            market_value = qty * current_price
            total_invested += market_value
            cash_balance += pos.get("cash_balance", 0)
            upnl = (current_price - avg_price) * qty
            ret_pct = ((current_price / avg_price - 1) * 100) if avg_price > 0 else 0.0
            positions_detail.append(
                {
                    "stock_code": stock_code,
                    "quantity": qty,
                    "avg_buy_price": avg_price,
                    "current_price": current_price,
                    "market_value": market_value,
                    "unrealized_pnl": upnl,
                    "return_pct": round(ret_pct, 4),
                }
            )

        return {
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "total_value": total_invested + cash_balance,
            "cash_balance": cash_balance,
            "invested_value": total_invested,
            "positions_detail": positions_detail,
            "pnl": pnl,
        }
