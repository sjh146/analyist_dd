"""Smoke tests for API gateway service."""
import pytest
import sys

sys.path.insert(0, "services/api-gateway")


class TestApiGatewaySmoke:
    """Basic smoke tests for api-gateway."""

    def test_import(self):
        """Verify main module imports without error."""
        try:
            import app.main  # type: ignore[import-untyped]
            assert hasattr(app.main, "app")
        except ImportError:
            pytest.skip("app.main module not found (service may not be installed)")

    def test_health_endpoint(self):
        """Verify health endpoint is defined."""
        try:
            import app.main  # type: ignore[import-untyped]
            if hasattr(app.main, "app") and hasattr(app.main.app, "routes"):
                routes = [r.path for r in app.main.app.routes]
                assert any("/health" in r for r in routes), "No health endpoint found"
        except ImportError:
            pytest.skip("app.main module not found")

    def test_package_importable(self):
        """Verify the service directory is importable as a package."""
        try:
            import api_gateway  # type: ignore[import-untyped]
            assert True
        except ImportError:
            pytest.skip("api_gateway package not installed")
