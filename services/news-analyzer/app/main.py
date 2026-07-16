"""
News/SNS Analyzer Service
- Collects news articles from RSS feeds
- Analyzes articles via DeepSeek API (authenticity + sentiment)
- Stores results in PostgreSQL and Neo4j
"""

import asyncio
import logging
import schedule
import time
from datetime import datetime
from typing import List, Dict

from app.config import Config
from app.collectors.rss_collector import RssCollector
from app.analyzers.deepseek_analyzer import DeepSeekAnalyzer
from app.storage.postgres_storage import PostgresStorage
from app.storage.neo4j_storage import Neo4jStorage
from app.models.schemas import Article, AnalysisResult
from app.data_quality_integration import DataQualityIntegration
from app.metrics_integration import init_metrics, on_article_collected, on_article_analyzed, sentiment_analysis_total

logging.basicConfig(level=Config.LOG_LEVEL)
logger = logging.getLogger(__name__)


class NewsAnalyzerService:
    def __init__(self):
        logger.info("Initializing News/SNS Analyzer Service...")
        self.config = Config()
        self.collector = RssCollector()
        self.analyzer = DeepSeekAnalyzer(api_key=self.config.DEEPSEEK_API_KEY)
        self.pg_storage = PostgresStorage()
        self.neo4j_storage = Neo4jStorage()
        self.dq_integration = DataQualityIntegration(
            db_conn_provider=self.pg_storage._get_conn
        )
        self._validated_at_ready = self.pg_storage._ensure_validated_at_column()
        init_metrics(9101)
        self._backfill_sentiment_metrics()
        self._running = False

    def _backfill_sentiment_metrics(self):
        """One-time backfill of sentiment metrics from existing DB records."""
        try:
            conn = self.pg_storage._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(sentiment_label, 'unknown'), count(*) "
                "FROM news_analysis GROUP BY sentiment_label"
            )
            rows = cur.fetchall()
            cur.close()
            self.pg_storage._put_conn(conn)
            for label, cnt in rows:
                sentiment_analysis_total.labels(
                    source="deepseek", sentiment=label
                ).inc(cnt)
                logger.info(f"Backfilled sentiment metric: {label}={cnt}")
        except Exception as e:
            logger.warning(f"Could not backfill sentiment metrics: {e}")

    async def analyze_recent_articles(self):
        """Collect and analyze recent articles from all sources."""
        logger.info("Starting article collection and analysis...")

        # Step 1: Collect articles
        articles = await self.collector.collect_all()
        logger.info(f"Collected {len(articles)} articles")
        on_article_collected()  # track collection metrics
        # Step 2: Analyze each article via DeepSeek
        for article in articles:
            try:
                # Check if already analyzed (dedup by URL)
                existing = self.pg_storage.get_analysis_by_url(article.url)
                if existing:
                    logger.debug(f"Already analyzed: {article.title[:50]}")
                    continue

                # Analyze
                import time as _time
                _t0 = _time.time()
                result = await self.analyzer.analyze_article(article)
                _t1 = _time.time()
                logger.info(
                    f"Analyzed: {article.title[:50]}... | "
                    f"Authenticity: {result.authenticity_label} "
                    f"({result.authenticity_score:.2f}) | "
                    f"Sentiment: {result.sentiment_label} "
                    f"({result.sentiment_score:.2f})"
                )
                on_article_analyzed(duration=_t1 - _t0, sentiment_label=result.sentiment_label)

                self.pg_storage.save_news_analysis(article, result)
                logger.debug(f"Saved to PostgreSQL: {article.title[:50]}")

                validated_at = datetime.now() if self._validated_at_ready else None
                validation_errors = []

                for stock_code in result.related_stocks:
                    try:
                        v_result = self.dq_integration.validate_sentiment(
                            sentiment_score=result.sentiment_score,
                            stock_code=stock_code,
                        )
                        self.dq_integration.log_validation_result(
                            sentiment_score=result.sentiment_score,
                            stock_code=stock_code,
                            article_title=article.title or "",
                            validation_result=v_result,
                        )
                    except Exception as ve:
                        logger.error(
                            f"Validation error for {stock_code}: {ve}"
                        )
                        v_result = {"passed": 0, "failed": 0, "warned": 1, "details": []}
                        validation_errors.append(str(ve))

                    self.pg_storage.save_stock_sentiment(
                        stock_code=stock_code,
                        date=datetime.now().date(),
                        sentiment_score=result.sentiment_score,
                        is_news=(article.source != "sns"),
                        validated_at=validated_at,
                    )

                    # Step 5: Update Neo4j relationships
                    self.neo4j_storage.save_sentiment_relationship(
                        stock_code=stock_code,
                        sentiment_score=result.sentiment_score,
                        date=datetime.now(),
                    )

                # Rate limit: 1 request per second
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error processing article '{article.title[:50]}': {e}")
                continue

        logger.info(f"Analysis cycle complete. Processed {len(articles)} articles.")

    def run_scheduled(self):
        # Run every 30 minutes
        schedule.every(30).minutes.do(
            lambda: asyncio.run(self.analyze_recent_articles())
        )

        logger.info("News Analyzer Service started. Running every 30 minutes.")
        self._running = True

        # Run once immediately on startup
        asyncio.run(self.analyze_recent_articles())

        while self._running:
            schedule.run_pending()
            time.sleep(60)

    def stop(self):
        self._running = False


def main():
    service = NewsAnalyzerService()
    try:
        service.run_scheduled()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        service.stop()


if __name__ == "__main__":
    main()
