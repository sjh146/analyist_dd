"""Shared fixtures for trade-executor tests."""

from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture
def mock_ib_insync():
    """Mock ib_insync module and its classes."""
    with patch.dict("sys.modules", {"ib_insync": MagicMock()}) as mock_modules:
        import ib_insync
        ib_insync.IB = MagicMock()
        ib_insync.Stock = MagicMock()
        ib_insync.MarketOrder = MagicMock()
        ib_insync.LimitOrder = MagicMock()

        class FakeAction:
            BUY = "BUY"
            SELL = "SELL"
        ib_insync.Action = FakeAction

        yield ib_insync


@pytest.fixture
def mock_ccxt():
    """Mock ccxt module."""
    with patch.dict("sys.modules", {"ccxt": MagicMock()}) as mock_modules:
        import ccxt
        yield ccxt


@pytest.fixture
def mock_ib_executor(mock_ib_insync):
    """Provide an IBExecutor instance with ib_insync mocked."""
    from executors.ib_executor import IBExecutor

    executor = IBExecutor(host="127.0.0.1", port=7497, client_id=1)
    mock_ib = mock_ib_insync.IB.return_value
    mock_ib.isConnected.return_value = True
    executor._ib = mock_ib
    executor._connected = True
    return executor


@pytest.fixture
def mock_ccxt_executor(mock_ccxt):
    """Provide a CCXTExecutor instance with ccxt mocked."""
    from executors.ccxt_executor import CCXTExecutor

    executor = CCXTExecutor(exchange_id="binance", api_key="test", api_secret="test")
    mock_exchange = MagicMock()
    executor._exchange = mock_exchange
    return executor
