import math
import pytest
from datetime import datetime
from portfolio.portfolio_tracker import PortfolioTracker


class TestCalculatePnl:
    def test_basic_pnl(self):
        tracker = PortfolioTracker()
        positions = [
            {"stock_code": "005930", "quantity": 10, "avg_buy_price": 70000},
            {"stock_code": "000660", "quantity": 5, "avg_buy_price": 150000},
        ]
        prices = {"005930": 75000, "000660": 140000}
        result = tracker.calculate_pnl(positions, prices)
        assert result["unrealized_pnl"] == (75000 - 70000) * 10 + (140000 - 150000) * 5
        assert result["realized_pnl"] == 0.0
        assert result["total_pnl"] == result["unrealized_pnl"]

    def test_with_realized_pnl(self):
        tracker = PortfolioTracker()
        positions = [
            {"stock_code": "005930", "quantity": 10, "avg_buy_price": 70000, "realized_pnl": 50000},
        ]
        prices = {"005930": 75000}
        result = tracker.calculate_pnl(positions, prices)
        assert result["unrealized_pnl"] == (75000 - 70000) * 10
        assert result["realized_pnl"] == 50000
        assert result["total_pnl"] == 50000 + 50000

    def test_empty_positions(self):
        tracker = PortfolioTracker()
        result = tracker.calculate_pnl([], {})
        assert result["unrealized_pnl"] == 0.0
        assert result["realized_pnl"] == 0.0
        assert result["total_pnl"] == 0.0

    def test_missing_price_defaults_zero(self):
        tracker = PortfolioTracker()
        positions = [
            {"stock_code": "005930", "quantity": 10, "avg_buy_price": 70000},
        ]
        result = tracker.calculate_pnl(positions, {})
        assert result["unrealized_pnl"] == -70000 * 10
        assert result["total_pnl"] == -700000

    def test_all_loss(self):
        tracker = PortfolioTracker()
        positions = [
            {"stock_code": "005930", "quantity": 10, "avg_buy_price": 100000},
        ]
        prices = {"005930": 80000}
        result = tracker.calculate_pnl(positions, prices)
        assert result["unrealized_pnl"] == -200000


class TestCalculateReturns:
    def test_basic_returns(self):
        tracker = PortfolioTracker()
        history = [
            {"date": "2024-01-01", "total_value": 1000000},
            {"date": "2024-01-02", "total_value": 1010000},
            {"date": "2024-01-03", "total_value": 1020000},
        ]
        result = tracker.calculate_returns(history)
        assert len(result["daily_returns"]) == 2
        assert result["daily_returns"][0] == pytest.approx(0.01)
        assert result["daily_returns"][1] == pytest.approx(0.00990099, rel=1e-3)
        assert result["cumulative_return"] == pytest.approx(0.02)
        assert result["cagr"] > 0

    def test_single_entry(self):
        tracker = PortfolioTracker()
        history = [{"date": "2024-01-01", "total_value": 1000000}]
        result = tracker.calculate_returns(history)
        assert result["daily_returns"] == []
        assert result["cumulative_return"] == 0.0
        assert result["cagr"] == 0.0

    def test_empty_history(self):
        tracker = PortfolioTracker()
        result = tracker.calculate_returns([])
        assert result["daily_returns"] == []
        assert result["cumulative_return"] == 0.0
        assert result["cagr"] == 0.0

    def test_zero_initial_value(self):
        tracker = PortfolioTracker()
        history = [
            {"date": "2024-01-01", "total_value": 0},
            {"date": "2024-01-02", "total_value": 1000000},
        ]
        result = tracker.calculate_returns(history)
        assert result["daily_returns"] == [0.0]
        assert result["cumulative_return"] == 0.0
        assert result["cagr"] == 0.0

    def test_negative_return(self):
        tracker = PortfolioTracker()
        history = [
            {"date": "2024-01-01", "total_value": 1000000},
            {"date": "2024-01-02", "total_value": 950000},
        ]
        result = tracker.calculate_returns(history)
        assert result["daily_returns"][0] == pytest.approx(-0.05)
        assert result["cumulative_return"] == pytest.approx(-0.05)


class TestCalculateMetrics:
    def test_basic_metrics(self):
        tracker = PortfolioTracker()
        returns = [0.001, -0.0005, 0.002, -0.001, 0.0015] * 50
        result = tracker.calculate_metrics(returns)
        assert result["sharpe_ratio"] > 0
        assert result["sortino_ratio"] > 0
        assert result["volatility"] > 0
        assert 0 < result["win_rate"] < 1

    def test_empty_series(self):
        tracker = PortfolioTracker()
        result = tracker.calculate_metrics([])
        assert all(v == 0.0 for v in result.values())

    def test_single_value(self):
        tracker = PortfolioTracker()
        result = tracker.calculate_metrics([0.01])
        assert result["win_rate"] == 1.0
        assert result["volatility"] == 0.0
        assert result["sharpe_ratio"] == 0.0

    def test_all_negative(self):
        tracker = PortfolioTracker()
        returns = [-0.01] * 10
        result = tracker.calculate_metrics(returns)
        assert result["win_rate"] == 0.0
        assert result["profit_factor"] == 0.0
        assert result["max_drawdown"] > 0

    def test_all_positive(self):
        tracker = PortfolioTracker()
        returns = [0.01] * 10
        result = tracker.calculate_metrics(returns)
        assert result["win_rate"] == 1.0
        assert result["profit_factor"] == float("inf")
        assert result["max_drawdown"] == 0.0

    def test_mixed_returns(self):
        tracker = PortfolioTracker()
        returns = [0.02, -0.01, 0.03, -0.02, 0.01]
        result = tracker.calculate_metrics(returns)
        assert 0 < result["win_rate"] < 1
        assert result["profit_factor"] > 0
        assert result["max_drawdown"] > 0
        assert result["volatility"] > 0

    def test_sharpe_sortino_known_values(self):
        tracker = PortfolioTracker()
        returns = [0.01, -0.005, 0.02]
        result = tracker.calculate_metrics(returns)
        assert result["sharpe_ratio"] != 0.0
        assert result["sortino_ratio"] != 0.0

    def test_sharpe_with_variance(self):
        tracker = PortfolioTracker()
        returns = [0.01, -0.005, 0.02, -0.01, 0.015, -0.008, 0.012, -0.003, 0.018, -0.006]
        result = tracker.calculate_metrics(returns)
        assert result["sharpe_ratio"] != 0.0
        assert result["sortino_ratio"] != 0.0
        # Sortino should be >= Sharpe for this data (no extreme downside)
        assert result["sortino_ratio"] >= result["sharpe_ratio"] or abs(
            result["sortino_ratio"] - result["sharpe_ratio"]
        ) < 0.01


class TestSnapshot:
    def test_basic_snapshot(self):
        tracker = PortfolioTracker()
        positions = [
            {"stock_code": "005930", "quantity": 10, "avg_buy_price": 70000},
            {"stock_code": "000660", "quantity": 5, "avg_buy_price": 150000, "cash_balance": 500000},
        ]
        prices = {"005930": 75000, "000660": 140000}
        result = tracker.snapshot(positions, prices, timestamp="2024-01-01T00:00:00")
        assert result["timestamp"] == "2024-01-01T00:00:00"
        assert result["invested_value"] == 75000 * 10 + 140000 * 5
        assert result["cash_balance"] == 500000
        assert result["total_value"] == result["invested_value"] + result["cash_balance"]
        assert len(result["positions_detail"]) == 2
        assert result["pnl"]["unrealized_pnl"] == (75000 - 70000) * 10 + (140000 - 150000) * 5

    def test_empty_positions_snapshot(self):
        tracker = PortfolioTracker()
        result = tracker.snapshot([], {}, timestamp="2024-01-01")
        assert result["total_value"] == 0.0
        assert result["cash_balance"] == 0.0
        assert result["positions_detail"] == []
        assert result["pnl"]["total_pnl"] == 0.0

    def test_snapshot_without_timestamp(self):
        tracker = PortfolioTracker()
        result = tracker.snapshot([], {})
        assert "timestamp" in result
        # Should be ISO format
        assert "T" in result["timestamp"]

    def test_snapshot_return_pct(self):
        tracker = PortfolioTracker()
        positions = [
            {"stock_code": "005930", "quantity": 10, "avg_buy_price": 50000},
        ]
        prices = {"005930": 60000}
        result = tracker.snapshot(positions, prices, timestamp="2024-01-01")
        detail = result["positions_detail"][0]
        assert detail["return_pct"] == 20.0  # (60000/50000 - 1) * 100

    def test_snapshot_zero_avg_price(self):
        tracker = PortfolioTracker()
        positions = [
            {"stock_code": "005930", "quantity": 10, "avg_buy_price": 0},
        ]
        prices = {"005930": 60000}
        result = tracker.snapshot(positions, prices, timestamp="2024-01-01")
        detail = result["positions_detail"][0]
        assert detail["return_pct"] == 0.0
