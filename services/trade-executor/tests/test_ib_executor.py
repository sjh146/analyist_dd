"""Tests for IBExecutor."""

import pytest
from unittest.mock import MagicMock, patch


class TestIBExecutorConnect:
    def test_connect_success(self, mock_ib_insync):
        from executors.ib_executor import IBExecutor

        executor = IBExecutor()
        mock_ib = mock_ib_insync.IB.return_value
        result = executor.connect()
        assert result is True
        assert executor._connected is True
        mock_ib.connect.assert_called_once_with("127.0.0.1", 7497, clientId=1)

    def test_connect_import_error(self):
        from executors.ib_executor import IBExecutor

        executor = IBExecutor()
        with patch.dict("sys.modules", {"ib_insync": None}):
            with pytest.raises(RuntimeError, match="ib_insync is not installed"):
                executor.connect()

    def test_connect_failure(self, mock_ib_insync):
        from executors.ib_executor import IBExecutor

        executor = IBExecutor()
        mock_ib = mock_ib_insync.IB.return_value
        mock_ib.connect.side_effect = ConnectionRefusedError("refused")
        with pytest.raises(RuntimeError, match="IB connection failed"):
            executor.connect()
        assert executor._connected is False

    def test_disconnect(self, mock_ib_executor):
        executor = mock_ib_executor
        executor.disconnect()
        executor._ib.disconnect.assert_called_once()
        assert executor._connected is False

    def test_is_connected_true(self, mock_ib_executor):
        assert mock_ib_executor.is_connected() is True

    def test_is_connected_false(self, mock_ib_insync):
        from executors.ib_executor import IBExecutor

        executor = IBExecutor()
        assert executor.is_connected() is False

    def test_is_connected_no_ib(self, mock_ib_insync):
        from executors.ib_executor import IBExecutor

        executor = IBExecutor()
        executor._ib = None
        assert executor.is_connected() is False


class TestIBExecutorOrders:
    def test_buy_order_market(self, mock_ib_executor):
        executor = mock_ib_executor
        mock_trade = MagicMock()
        mock_trade.order.orderId = 1001
        mock_trade.orderStatus.status = "Filled"
        mock_trade.orderStatus.filled = 10
        mock_trade.orderStatus.avgFillPrice = 150.0
        executor._ib.qualifyContracts.return_value = [MagicMock()]
        executor._ib.placeOrder.return_value = mock_trade

        result = executor.buy_order("AAPL", 10, order_type="MKT")
        assert result["order_id"] == "1001"
        assert result["status"] == "Filled"
        assert result["filled_qty"] == 10
        assert result["avg_price"] == 150.0
        assert result["action"] == "buy"

    def test_buy_order_limit(self, mock_ib_executor):
        executor = mock_ib_executor
        mock_trade = MagicMock()
        mock_trade.order.orderId = 1002
        mock_trade.orderStatus.status = "Submitted"
        mock_trade.orderStatus.filled = 0
        mock_trade.orderStatus.avgFillPrice = 0.0
        executor._ib.qualifyContracts.return_value = [MagicMock()]
        executor._ib.placeOrder.return_value = mock_trade

        result = executor.buy_order("AAPL", 10, order_type="LMT", price=150.0)
        assert result["order_id"] == "1002"
        assert result["action"] == "buy"

    def test_sell_order(self, mock_ib_executor):
        executor = mock_ib_executor
        mock_trade = MagicMock()
        mock_trade.order.orderId = 2001
        mock_trade.orderStatus.status = "Filled"
        mock_trade.orderStatus.filled = 5
        mock_trade.orderStatus.avgFillPrice = 200.0
        executor._ib.qualifyContracts.return_value = [MagicMock()]
        executor._ib.placeOrder.return_value = mock_trade

        result = executor.sell_order("AAPL", 5, order_type="MKT")
        assert result["order_id"] == "2001"
        assert result["filled_qty"] == 5
        assert result["action"] == "sell"

    def test_order_not_connected(self):
        from executors.ib_executor import IBExecutor

        executor = IBExecutor()
        with pytest.raises(RuntimeError, match="Not connected to IB"):
            executor.buy_order("AAPL", 10)

    def test_qualify_contracts_fails(self, mock_ib_executor):
        executor = mock_ib_executor
        executor._ib.qualifyContracts.return_value = []
        with pytest.raises(RuntimeError, match="Could not qualify contract"):
            executor.buy_order("AAPL", 10)

    def test_cancel_order_success(self, mock_ib_executor):
        executor = mock_ib_executor
        mock_order = MagicMock()
        mock_order.orderId = 3001
        executor._ib.orders.return_value = [mock_order]

        result = executor.cancel_order("3001")
        assert result is True
        executor._ib.cancelOrder.assert_called_once_with(mock_order)

    def test_cancel_order_not_found(self, mock_ib_executor):
        executor = mock_ib_executor
        executor._ib.orders.return_value = []

        result = executor.cancel_order("9999")
        assert result is False

    def test_cancel_order_not_connected(self):
        from executors.ib_executor import IBExecutor

        executor = IBExecutor()
        with pytest.raises(RuntimeError, match="Not connected to IB"):
            executor.cancel_order("3001")


class TestIBExecutorAccount:
    def test_get_positions(self, mock_ib_executor):
        executor = mock_ib_executor
        mock_position = MagicMock()
        mock_position.contract.symbol = "AAPL"
        mock_position.position = 100
        mock_position.avgCost = 150.0
        mock_position.marketPrice = 160.0
        mock_position.marketValue = 16000.0
        executor._ib.positions.return_value = [mock_position]

        positions = executor.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "AAPL"
        assert positions[0]["position"] == 100
        assert positions[0]["avg_cost"] == 150.0

    def test_get_account_summary(self, mock_ib_executor):
        executor = mock_ib_executor
        mock_item = lambda tag, val: type("Item", (), {"tag": tag, "value": val})()
        items = [
            mock_item("TotalCashValue", "50000"),
            mock_item("BuyingPower", "100000"),
            mock_item("GrossPositionValue", "200000"),
            mock_item("NetLiquidation", "250000"),
        ]
        executor._ib.accountSummary.return_value = items

        summary = executor.get_account_summary()
        assert summary["total_cash"] == 50000.0
        assert summary["buying_power"] == 100000.0
        assert summary["gross_position_value"] == 200000.0
        assert summary["net_liquidation"] == 250000.0

    def test_get_positions_not_connected(self):
        from executors.ib_executor import IBExecutor

        executor = IBExecutor()
        with pytest.raises(RuntimeError, match="Not connected to IB"):
            executor.get_positions()
