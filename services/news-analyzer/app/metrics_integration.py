"""News-analyzer Prometheus metrics integration."""

import logging
from prometheus_client import Counter, Histogram, start_http_server

logger = logging.getLogger(__name__)

# ── Shared metrics (inlined from services.shared.metrics) ──────────────
data_collected_total = Counter('data_collected_total', 'Total data collected', ['service', 'source'])
features_computed_total = Counter('features_computed_total', 'Total features computed', ['service'])
feature_count_gauge = None  # not used by this service
prediction_latency_seconds = Histogram('prediction_latency_seconds', 'Prediction latency', ['model'], buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0])
sentiment_analysis_total = Counter('sentiment_analysis_total', 'Sentiment analyses', ['source', 'sentiment'])
signal_generated_total = Counter('signal_generated_total', 'Trading signals generated')
trade_executed_total = Counter('trade_executed_total', 'Trades executed')
db_query_latency_seconds = Histogram('db_query_latency_seconds', 'DB query latency', buckets=[0.001, 0.01, 0.1, 0.5, 1.0])
redis_publish_total = Counter('redis_publish_total', 'Redis messages published', ['stream'])

# ── Service-specific metrics ──────────────────────────────────────────
articles_collected_total = data_collected_total
articles_analyzed_total = sentiment_analysis_total
sentiment_analysis_duration_seconds = Histogram(
    "sentiment_analysis_duration_seconds",
    "Time spent per sentiment analysis call (seconds)",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)


def init_metrics(port: int = 9101) -> None:
    """Start the Prometheus HTTP metrics endpoint on *port*."""
    try:
        start_http_server(port)
        logger.info(f"Prometheus metrics server started on port {port}")
    except OSError as e:
        logger.warning(f"Could not start metrics server on port {port}: {e}")


def on_article_collected() -> None:
    """Increment the articles-collected counter."""
    try:
        articles_collected_total.labels(
            service="news-analyzer", source="naver_news"
        ).inc()
    except Exception:
        logger.debug("Failed to increment collected counter", exc_info=True)


# ── News intelligence pipeline metrics (Phase 2-7) ─────────────────────
news_articles_collected_total = Counter(
    "news_articles_collected_total", "News articles collected (post-dedup)", ["source"]
)
news_dedupe_dropped_total = Counter(
    "news_dedupe_dropped_total", "Duplicate articles dropped by dedup", ["source"]
)
news_extractions_total = Counter(
    "news_extractions_total", "Structured event extractions saved (DeepSeek)", ["event_type"]
)
news_clusters_total = Counter(
    "news_clusters_total", "Event clusters upserted into news_events", []
)
news_embeddings_total = Counter(
    "news_embeddings_total", "Event embeddings stored in pgvector", []
)


def on_articles_collected(count: int = 1, source: str = "rss") -> None:
    try:
        news_articles_collected_total.labels(source=source).inc(count)
    except Exception:
        logger.debug("Failed to record collected metric", exc_info=True)


def on_dedupe_dropped(count: int = 1, source: str = "rss") -> None:
    try:
        news_dedupe_dropped_total.labels(source=source).inc(count)
    except Exception:
        logger.debug("Failed to record dedupe metric", exc_info=True)


def on_extraction_saved(event_type: str = "기타") -> None:
    try:
        news_extractions_total.labels(event_type=event_type).inc()
    except Exception:
        logger.debug("Failed to record extraction metric", exc_info=True)


def on_cluster_saved() -> None:
    try:
        news_clusters_total.inc()
    except Exception:
        logger.debug("Failed to record cluster metric", exc_info=True)


def on_embedding_saved() -> None:
    try:
        news_embeddings_total.inc()
    except Exception:
        logger.debug("Failed to record embedding metric", exc_info=True)


def on_article_analyzed(duration: float = 0.0, sentiment_label: str = "") -> None:
    """Increment the analyzed counter and observe the duration."""
    try:
        articles_analyzed_total.labels(
            source="deepseek", sentiment=sentiment_label
        ).inc()
        if duration > 0:
            sentiment_analysis_duration_seconds.observe(duration)
    except Exception:
        logger.debug("Failed to record analysis metric", exc_info=True)
