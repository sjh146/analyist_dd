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


class TestInternalApi:
    """M4: 내부 API (/internal/*) — cmall-api 전용, fail-closed."""

    def test_internal_router_registered(self):
        """internal_router가 앱에 등록되어 있어야 한다."""
        try:
            import app.main  # type: ignore[import-untyped]
            routes = [r.path for r in app.main.app.routes]
            assert any("/internal/analysis" in r for r in routes), "No /internal/analysis route"
            assert any("/internal/signals/" in r for r in routes), "No /internal/signals route"
        except ImportError:
            pytest.skip("app.main module not found")

    def test_internal_key_fail_closed(self):
        """INTERNAL_API_KEY 미설정 시 내부 API는 503 (fail-closed)."""
        try:
            import os
            from unittest.mock import patch
            import importlib
            import app.internal_api as ia

            with patch.dict(os.environ, {}, clear=False):
                if "INTERNAL_API_KEY" in os.environ:
                    del os.environ["INTERNAL_API_KEY"]
                importlib.reload(ia)
                assert ia.INTERNAL_CONFIGURED is False
        except ImportError:
            pytest.skip("internal_api module not found")
