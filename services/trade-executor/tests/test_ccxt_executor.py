"""Tests for CCXTExecutor."""

import pytest
from unittest.mock import MagicMock, patch


class TestCCXTExecutorConnect:
    def test_connect_success(self, mock_ccxt):
        from executors.ccxt_executor import CCXTExecutor

        executor = CCXTExecutor(exchange_id="binance", api_key="k", api_secret="s")
        executor.connect()
        assert executor._exchange is not None

    def test_connect_import_error(self):
        from executors.ccxt_executor import CCXTExecutor

        executor = CCXTExecutor()
        with patch.dict("sys.modules", {"ccxt": None}):
            with pytest.raises(RuntimeError, match="ccxt is not installed"):
                executor.connect()

    def test_connect_unknown_exchange(self, mock_ccxt):
        from executors.ccxt_executor import CCXTExecutor

        del mock_ccxt.nonexistent_exchange
        executor = CCXTExecutor(exchange_id="nonexistent_exchange")
        with pytest.raises(RuntimeError, match="Unknown exchange"):
            executor.connect()

    def test_connect_api_error(self, mock_ccxt):
        from executors.ccxt_executor import CCXTExecutor

        mock_ccxt.binance.side_effect = Exception("API error")
        executor = CCXTExecutor(exchange_id="binance")
        with pytest.raises(RuntimeError, match="CCXT connection failed"):
            executor.connect()


class TestCCXTExecutorOrders:
    def test_buy_order_market(self, mock_ccxt_executor):
        executor = mock_ccxt_executor
        executor._exchange.create_order.return_value = {
            "id": "12345",
            "status": "closed",
            "filled": 1.0,
            "average": 50000.0,
        }

        result = executor.buy_order("BTC/USDT", 1.0, order_type="market")
        assert result["order_id"] == "12345"
        assert result["status"] == "closed"
        assert result["filled_qty"] == 1.0
        assert result["avg_price"] == 50000.0
        assert result["action"] == "buy"
        executor._exchange.create_order.assert_called_once_with(
            symbol="BTC/USDT", type="market", side="buy", amount=1.0, price=None
        )

    def test_buy_order_limit(self, mock_ccxt_executor):
        executor = mock_ccxt_executor
        executor._exchange.create_order.return_value = {
            "id": "12346",
            "status": "open",
            "filled": 0.0,
            "average": 0.0,
        }

        result = executor.buy_order("BTC/USDT", 0.5, order_type="limit", price=49000.0)
        assert result["order_id"] == "12346"
        executor._exchange.create_order.assert_called_once_with(
            symbol="BTC/USDT", type="limit", side="buy", amount=0.5, price=49000.0
        )

    def test_sell_order(self, mock_ccxt_executor):
        executor = mock_ccxt_executor
        executor._exchange.create_order.return_value = {
            "id": "54321",
            "status": "closed",
            "filled": 2.0,
            "average": 51000.0,
        }

        result = executor.sell_order("BTC/USDT", 2.0, order_type="market")
        assert result["order_id"] == "54321"
        assert result["filled_qty"] == 2.0
        assert result["action"] == "sell"

    def test_cancel_order_success(self, mock_ccxt_executor):
        executor = mock_ccxt_executor
        result = executor.cancel_order("12345")
        assert result is True
        executor._exchange.cancel_order.assert_called_once_with("12345")

    def test_cancel_order_failure(self, mock_ccxt_executor):
        executor = mock_ccxt_executor
        executor._exchange.cancel_order.side_effect = Exception("Network error")
        result = executor.cancel_order("12345")
        assert result is False

    def test_order_auto_connect(self, mock_ccxt):
        from executors.ccxt_executor import CCXTExecutor

        executor = CCXTExecutor(exchange_id="binance", api_key="k", api_secret="s")
        mock_exchange = MagicMock()
        mock_exchange.create_order.return_value = {"id": "1", "status": "closed", "filled": 1.0, "average": 100.0}
        executor._exchange = mock_exchange
        result = executor.buy_order("BTC/USDT", 1.0)
        assert result["order_id"] == "1"


class TestCCXTExecutorAccount:
    def test_get_balance(self, mock_ccxt_executor):
        executor = mock_ccxt_executor
        executor._exchange.fetch_balance.return_value = {
            "total": {"BTC": 1.5, "USDT": 50000},
            "free": {"BTC": 1.0, "USDT": 30000},
            "used": {"BTC": 0.5, "USDT": 20000},
        }

        balance = executor.get_balance()
        assert balance["BTC"]["total"] == 1.5
        assert balance["BTC"]["free"] == 1.0
        assert balance["BTC"]["used"] == 0.5
        assert balance["USDT"]["total"] == 50000

    def test_get_ticker(self, mock_ccxt_executor):
        executor = mock_ccxt_executor
        executor._exchange.fetch_ticker.return_value = {
            "bid": 50000.0,
            "ask": 50100.0,
            "last": 50050.0,
            "baseVolume": 10000.0,
        }

        ticker = executor.get_ticker("BTC/USDT")
        assert ticker["bid"] == 50000.0
        assert ticker["ask"] == 50100.0
        assert ticker["last"] == 50050.0
        assert ticker["volume"] == 10000.0

    def test_get_positions(self, mock_ccxt_executor):
        executor = mock_ccxt_executor
        executor._exchange.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT",
                "contracts": 1.0,
                "entryPrice": 45000.0,
                "markPrice": 50000.0,
                "unrealizedPnl": 5000.0,
            }
        ]

        positions = executor.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTC/USDT"
        assert positions[0]["position"] == 1.0
        assert positions[0]["pnl"] == 5000.0
