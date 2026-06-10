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
        self._running = False

    async def analyze_recent_articles(self):
        """Collect and analyze recent articles from all sources."""
        logger.info("Starting article collection and analysis...")

        # Step 1: Collect articles
        articles = await self.collector.collect_all()
        logger.info(f"Collected {len(articles)} articles")

        # Step 2: Analyze each article via DeepSeek
        for article in articles:
            try:
                # Check if already analyzed (dedup by URL)
                existing = self.pg_storage.get_analysis_by_url(article.url)
                if existing:
                    logger.debug(f"Already analyzed: {article.title[:50]}")
                    continue

                # Analyze
                result = await self.analyzer.analyze_article(article)
                logger.info(
                    f"Analyzed: {article.title[:50]}... | "
                    f"Authenticity: {result.authenticity_label} "
                    f"({result.authenticity_score:.2f}) | "
                    f"Sentiment: {result.sentiment_label} "
                    f"({result.sentiment_score:.2f})"
                )

                # Step 3: Store in PostgreSQL
                self.pg_storage.save_news_analysis(article, result)
                logger.debug(f"Saved to PostgreSQL: {article.title[:50]}")

                # Step 4: Update stock sentiment if related stocks found
                for stock_code in result.related_stocks:
                    self.pg_storage.save_stock_sentiment(
                        stock_code=stock_code,
                        date=datetime.now().date(),
                        sentiment_score=result.sentiment_score,
                        is_news=(article.source != "sns"),
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
        """Run analysis on a schedule."""
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
