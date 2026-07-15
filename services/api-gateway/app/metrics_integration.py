"""Prometheus metrics integration for API Gateway service."""

import logging

from prometheus_client import Counter, Histogram, Gauge, start_http_server

logger = logging.getLogger(__name__)

# ── Shared metrics (inlined from services.shared.metrics) ──────────────
data_collected_total = Counter('data_collected_total', 'Total data collected', ['service', 'source'])
features_computed_total = Counter('features_computed_total', 'Total features computed', ['service'])
feature_count_gauge = Gauge('feature_count_gauge', 'Number of features', ['stock'])
prediction_latency_seconds = Histogram('prediction_latency_seconds', 'Prediction latency', ['model'], buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0])
sentiment_analysis_total = Counter('sentiment_analysis_total', 'Sentiment analyses', ['source'])
signal_generated_total = Counter('signal_generated_total', 'Trading signals generated')
trade_executed_total = Counter('trade_executed_total', 'Trades executed')
db_query_latency_seconds = Histogram('db_query_latency_seconds', 'DB query latency', buckets=[0.001, 0.01, 0.1, 0.5, 1.0])
redis_publish_total = Counter('redis_publish_total', 'Redis messages published', ['stream'])

# ── Service-specific metrics ──────────────────────────────────────────
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


def init_metrics(port: int = 9100):
    """Start Prometheus metrics server on given port."""
    try:
        start_http_server(port)
        logger.info(f"Prometheus metrics server started on port {port}")
    except OSError as e:
        logger.warning(f"Could not start metrics server on port {port}: {e}")


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
