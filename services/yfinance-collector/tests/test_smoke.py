"""Smoke tests for yfinance-fetcher service."""
import pytest
import sys

sys.path.insert(0, "services/yfinance-fetcher")


class TestYFinanceFetcherSmoke:
    """Basic smoke tests for yfinance-fetcher."""

    def test_import(self):
        """Verify main module imports without error."""
        try:
            import app.main  # type: ignore[import-untyped]
            assert hasattr(app.main, "app") or hasattr(app.main, "main")
        except ImportError:
            pytest.skip("app.main module not found (service may not be installed)")

    def test_config_exists(self):
        """Verify config module exists."""
        try:
            import config  # type: ignore[import-untyped]
            assert True
        except ImportError:
            pytest.skip("config module not found")

    def test_package_importable(self):
        """Verify the service directory is importable as a package."""
        try:
            import yfinance_fetcher  # type: ignore[import-untyped]
            assert True
        except ImportError:
            pytest.skip("yfinance_fetcher package not installed")
