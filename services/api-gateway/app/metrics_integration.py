"""Prometheus metrics integration for API Gateway service."""

import logging

from prometheus_client import Counter, Histogram

from services.shared.metrics import start_metrics_server

logger = logging.getLogger(__name__)

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)


def init_metrics():
    """Start Prometheus metrics server on port 9100."""
    start_metrics_server(9100)


def on_request(method: str, endpoint: str, status: int, duration_seconds: float):
    """Record an HTTP request with method, endpoint, status, and duration."""
    try:
        http_requests_total.labels(
            method=method, endpoint=endpoint, status=str(status)
        ).inc()
        http_request_duration_seconds.labels(
            method=method, endpoint=endpoint
        ).observe(duration_seconds)
    except Exception as e:
        logger.debug(f"Metrics error (http_request): {e}")
