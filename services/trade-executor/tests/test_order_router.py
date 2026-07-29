"""Tests for SmartOrderRouter."""

import pytest
from unittest.mock import MagicMock


class TestSmartOrderRouter:
    def test_register_broker(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        mock_broker = MagicMock()
        router.register_broker("ib", mock_broker)
        assert "ib" in router._brokers
        assert router._brokers["ib"] is mock_broker

    @pytest.mark.parametrize(
        "stock_code,expected_broker",
        [
            ("005930", "creon"),
            ("000660", "creon"),
            ("AAPL", "ib"),
            ("MSFT", "ib"),
            ("BTC-USD", "ccxt"),
            ("ETH-USDT", "ccxt"),
            ("TSLA", "ib"),
        ],
    )
    def test_detect_broker(self, stock_code, expected_broker):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        result = router._detect_broker(stock_code)
        assert result == expected_broker

    def test_route_korea_stock(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        mock_creon = MagicMock()
        mock_creon.buy_order.return_value = {"order_id": "C001", "status": "filled"}
        router.register_broker("creon", mock_creon)

        order = {"stock_code": "005930", "action": "buy", "quantity": 10, "price": 70000}
        result = router.route(order)
        assert result["broker"] == "creon"
        assert result["status"] == "success"
        mock_creon.buy_order.assert_called_once_with("005930", 10, "MKT", 70000)

    def test_route_us_stock(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        mock_ib = MagicMock()
        mock_ib.buy_order.return_value = {"order_id": "IB001", "status": "Filled"}
        router.register_broker("ib", mock_ib)

        order = {"stock_code": "AAPL", "action": "buy", "quantity": 10, "price": 150.0}
        result = router.route(order)
        assert result["broker"] == "ib"
        assert result["status"] == "success"
        mock_ib.buy_order.assert_called_once_with("AAPL", 10, "MKT", 150.0)

    def test_route_crypto(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        mock_ccxt = MagicMock()
        mock_ccxt.buy_order.return_value = {"order_id": "CCXT001", "status": "closed"}
        router.register_broker("ccxt", mock_ccxt)

        order = {"symbol": "BTC-USD", "action": "buy", "qty": 1.0, "price": 50000}
        result = router.route(order)
        assert result["broker"] == "ccxt"
        assert result["status"] == "success"

    def test_route_sell(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        mock_ib = MagicMock()
        mock_ib.sell_order.return_value = {"order_id": "IB002", "status": "Filled"}
        router.register_broker("ib", mock_ib)

        result = router.route({"symbol": "AAPL", "action": "sell", "qty": 5, "price": 160.0})
        assert result["broker"] == "ib"
        mock_ib.sell_order.assert_called_once()

    def test_route_no_broker_registered(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        result = router.route({"stock_code": "005930", "action": "buy", "quantity": 10, "price": 1000})
        assert result["status"] == "error"
        assert "No broker registered" in result["result"]["error"]

    def test_route_fallback_on_failure(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        mock_creon = MagicMock()
        mock_creon.buy_order.side_effect = Exception("Creon API error")
        mock_fallback = MagicMock()
        mock_fallback.buy_order.return_value = {"order_id": "M001", "status": "filled"}
        router.register_broker("creon", mock_creon)
        router.register_broker("mock", mock_fallback)

        order = {"stock_code": "005930", "action": "buy", "quantity": 10, "price": 1000}
        result = router.route(order)
        assert result["broker"] == "mock"
        assert result["status"] == "success"
        mock_fallback.buy_order.assert_called_once()

    def test_route_primary_fallback_also_fails(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        mock_creon = MagicMock()
        mock_creon.buy_order.side_effect = Exception("Creon error")
        mock_fallback = MagicMock()
        mock_fallback.buy_order.side_effect = Exception("Fallback error")
        router.register_broker("creon", mock_creon)
        router.register_broker("mock", mock_fallback)

        result = router.route({"stock_code": "005930", "action": "buy", "quantity": 10, "price": 1000})
        assert result["status"] == "error"
        assert "Primary failed" in result["result"]["error"]

    def test_route_unknown_action(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        mock_broker = MagicMock()
        router.register_broker("ib", mock_broker)

        result = router.route({"symbol": "AAPL", "action": "unknown", "qty": 10})
        assert result["status"] == "error"
        assert "Unknown action" in result["result"]["error"]

    def test_route_fallback_when_primary_not_registered(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter(config={"korea": "creon"})
        mock_fallback = MagicMock()
        mock_fallback.buy_order.return_value = {"order_id": "M002", "status": "filled"}
        router.register_broker("mock", mock_fallback)

        result = router.route({"stock_code": "005930", "action": "buy", "quantity": 10, "price": 1000})
        assert result["broker"] == "mock"
        assert result["status"] == "success"

    def test_best_execution_selects_lowest_cost(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        mock_broker_a = MagicMock()
        mock_broker_a.get_ticker.return_value = {"bid": 99.0, "ask": 101.0, "last": 100.0, "volume": 1000}
        mock_broker_b = MagicMock()
        mock_broker_b.get_ticker.return_value = {"bid": 99.5, "ask": 100.5, "last": 100.0, "volume": 1000}
        router.register_broker("broker_a", mock_broker_a)
        router.register_broker("broker_b", mock_broker_b)

        result = router.best_execution({"symbol": "AAPL", "action": "buy"}, ["broker_a", "broker_b"])
        assert result["broker"] == "broker_b"
        assert 1.0 < result["estimated_cost"] < 2.0  # broker_b spread ~1.005%

    def test_best_execution_no_brokers_available(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        result = router.best_execution({"symbol": "AAPL"}, ["nonexistent"])
        assert result["broker"] is None
        assert result["estimated_cost"] == float("inf")

    def test_best_execution_skips_brokers_without_ticker(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter()
        mock_broker = MagicMock()
        mock_broker.get_ticker.side_effect = Exception("No ticker")
        router.register_broker("bad", mock_broker)

        result = router.best_execution({"symbol": "AAPL"}, ["bad"])
        assert result["broker"] is None

    def test_config_custom_mapping(self):
        from executors.order_router import SmartOrderRouter

        router = SmartOrderRouter(config={"korea": "mock", "us": "mock", "crypto": "mock"})
        assert router.config["korea"] == "mock"
