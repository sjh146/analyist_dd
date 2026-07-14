"""News-analyzer Prometheus metrics integration.

Defines service-specific metrics and wraps shared metrics helpers
so the main module never crashes if the metrics stack fails.
"""

import logging
from prometheus_client import Histogram

from services.shared.metrics import (
    data_collected_total,
    sentiment_analysis_total,
    start_metrics_server,
)

logger = logging.getLogger(__name__)

# ── Service-specific metrics ──────────────────────────────────────────
articles_collected_total = data_collected_total
articles_analyzed_total = sentiment_analysis_total
sentiment_analysis_duration_seconds = Histogram(
    "sentiment_analysis_duration_seconds",
    "Time spent per sentiment analysis call (seconds)",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)


def init_metrics(port: int = 9101) -> None:
    """Start the Prometheus HTTP metrics endpoint on *port* (default 9101)."""
    try:
        start_metrics_server(port)
    except Exception:
        logger.exception("Failed to start metrics server — continuing without metrics")


def on_article_collected() -> None:
    """Increment the articles-collected counter (never raises)."""
    try:
        articles_collected_total.labels(
            service="news-analyzer", source="naver_news"
        ).inc()
    except Exception:
        logger.debug("Failed to increment collected counter", exc_info=True)


def on_article_analyzed(duration: float = 0.0) -> None:
    """Increment the analyzed counter and observe the duration (never raises)."""
    try:
        articles_analyzed_total.labels(source="deepseek").inc()
        if duration > 0:
            sentiment_analysis_duration_seconds.observe(duration)
    except Exception:
        logger.debug("Failed to record analysis metric", exc_info=True)
