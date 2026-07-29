"""
Tests for PortfolioRiskManager.
"""

import pytest
from unittest.mock import MagicMock
from app.risk_management.portfolio_risk import PortfolioRiskManager


class TestPortfolioRiskCorrelation:
    def test_no_pg_storage(self):
        mgr = PortfolioRiskManager()
        result = mgr.check_correlation("005930", [{"stock_code": "000660"}])
        assert result["approved"] is True
        assert "No existing positions" in result["reason"]

    def test_no_current_positions(self):
        mgr = PortfolioRiskManager(pg_storage=MagicMock())
        result = mgr.check_correlation("005930", [])
        assert result["approved"] is True

    def test_low_correlation_approved(self):
        mock_storage = MagicMock()
        mock_storage.get_price_series.side_effect = (
            lambda code, days: [100, 101, 102, 103, 104, 105, 106, 107, 108, 109] if code == "005930"
            else [100, 100, 100, 100, 100, 100, 100, 100, 100, 100]
        )
        mgr = PortfolioRiskManager(pg_storage=mock_storage)
        result = mgr.check_correlation("005930", [{"stock_code": "000660"}])
        assert result["approved"] is True

    def test_short_returns_skipped(self):
        mock_storage = MagicMock()
        mock_storage.get_price_series.return_value = [100]
        mgr = PortfolioRiskManager(pg_storage=mock_storage)
        result = mgr.check_correlation("005930", [{"stock_code": "000660"}])
        assert result["approved"] is True


class TestPortfolioRiskConcentration:
    def test_no_pg_storage(self):
        mgr = PortfolioRiskManager()
        result = mgr.check_concentration("005930", [{"stock_code": "000660", "quantity": 10, "avg_buy_price": 10000}])
        assert result["approved"] is True

    def test_no_existing_value(self):
        mock_storage = MagicMock()
        mock_storage.get_all_stocks.return_value = []
        mgr = PortfolioRiskManager(pg_storage=mock_storage)
        result = mgr.check_concentration("005930", [])
        assert result["approved"] is True

    def test_exceeds_single_limit(self):
        mock_storage = MagicMock()
        mock_storage.get_all_stocks.return_value = []
        mgr = PortfolioRiskManager(pg_storage=mock_storage)
        result = mgr.check_concentration("005930", [{"stock_code": "005930", "quantity": 1, "avg_buy_price": 10_000_000}], max_single=0.10)
        assert result["approved"] is False


class TestPortfolioRiskDrawdown:
    def test_insufficient_history(self):
        mgr = PortfolioRiskManager()
        result = mgr.check_drawdown([])
        assert result["approved"] is True

    def test_no_drawdown(self):
        mgr = PortfolioRiskManager()
        result = mgr.check_drawdown([100, 101, 102, 103])
        assert result["approved"] is True

    def test_drawdown_exceeded(self):
        mgr = PortfolioRiskManager()
        result = mgr.check_drawdown([100, 110, 90, 95, 80])
        assert result["approved"] is False

    def test_drawdown_below_threshold(self):
        mgr = PortfolioRiskManager()
        result = mgr.check_drawdown([100, 105, 102, 108, 106], max_drawdown=0.15)
        assert result["approved"] is True


class TestPortfolioRiskCheckAll:
    def test_all_approved(self):
        mgr = PortfolioRiskManager()
        result = mgr.check_all("005930", [])
        assert result["approved"] is True
        assert "correlation" in result
        assert "concentration" in result
        assert "drawdown" in result

    def test_correlation_rejected(self):
        mock_storage = MagicMock()
        mock_storage.get_price_series.side_effect = (
            lambda code, days: [100, 102, 104, 106, 108, 110, 112, 114, 116, 118] if code == "005930"
            else [100, 102, 104, 106, 108, 110, 112, 114, 116, 118]
        )
        mgr = PortfolioRiskManager(pg_storage=mock_storage)
        result = mgr.check_all("005930", [{"stock_code": "000660"}], portfolio_history=[100, 101, 102])
        assert result["correlation"]["approved"] is False
