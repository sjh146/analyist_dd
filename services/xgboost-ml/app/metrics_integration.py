"""Prometheus metrics integration for XGBoost ML service."""

import logging

from prometheus_client import Counter, Gauge, Histogram, start_http_server

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
model_version_gauge = Gauge(
    "model_version_gauge", "Current model version", ["version"]
)


def init_metrics(port: int = 9102):
    """Start Prometheus metrics server on given port."""
    try:
        start_http_server(port)
        logger.info(f"Prometheus metrics server started on port {port}")
    except OSError as e:
        logger.warning(f"Could not start metrics server on port {port}: {e}")


def on_features_computed(count: int):
    """Record number of features computed."""
    try:
        features_computed_total.labels(service="xgboost-ml").inc(count)
    except Exception as e:
        logger.debug(f"Metrics error (features_computed): {e}")


def on_feature_count(stock: str, count: int):
    """Set the feature count for a given stock."""
    try:
        feature_count_gauge.labels(stock=stock).set(count)
    except Exception as e:
        logger.debug(f"Metrics error (feature_count): {e}")


def on_prediction(latency_seconds: float):
    """Record prediction latency in seconds."""
    try:
        prediction_latency_seconds.labels(model="xgboost").observe(latency_seconds)
    except Exception as e:
        logger.debug(f"Metrics error (prediction_latency): {e}")


def set_model_version(version: str):
    """Set the active model version."""
    try:
        model_version_gauge.labels(version=version).set(1)
    except Exception as e:
        logger.debug(f"Metrics error (model_version): {e}")
