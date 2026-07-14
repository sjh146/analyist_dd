"""Prometheus metrics definitions for the trading analysis system."""

from prometheus_client import Counter, Gauge, Histogram

data_collected_total = Counter('data_collected_total', 'Total data collected', ['service', 'source'])
features_computed_total = Counter('features_computed_total', 'Total features computed', ['service'])
feature_count_gauge = Gauge('feature_count_gauge', 'Number of features', ['stock'])
prediction_latency_seconds = Histogram('prediction_latency_seconds', 'Prediction latency', ['model'], buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0])
sentiment_analysis_total = Counter('sentiment_analysis_total', 'Sentiment analyses', ['source'])
signal_generated_total = Counter('signal_generated_total', 'Trading signals generated')
trade_executed_total = Counter('trade_executed_total', 'Trades executed')
db_query_latency_seconds = Histogram('db_query_latency_seconds', 'DB query latency', buckets=[0.001, 0.01, 0.1, 0.5, 1.0])
redis_publish_total = Counter('redis_publish_total', 'Redis messages published', ['stream'])


def start_metrics_server(port: int = 9100):
    """Start Prometheus metrics HTTP server. Graceful if port in use."""
    from prometheus_client import start_http_server
    import logging
    logger = logging.getLogger(__name__)
    try:
        start_http_server(port)
        logger.info(f"Metrics server started on port {port}")
    except OSError as e:
        logger.warning(f"Could not start metrics server on port {port}: {e}")
