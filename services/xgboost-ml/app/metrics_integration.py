"""Prometheus metrics integration for XGBoost ML service."""

import logging

from prometheus_client import Gauge

from services.shared.metrics import (
    features_computed_total,
    feature_count_gauge,
    prediction_latency_seconds,
    start_metrics_server,
)

logger = logging.getLogger(__name__)

model_version_gauge = Gauge(
    "model_version_gauge", "Current model version", ["version"]
)


def init_metrics():
    """Start Prometheus metrics server on port 9102."""
    start_metrics_server(9102)


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
