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
from typing import List, Dict, Optional

from app.config import Config
from app.collectors.rss_collector import RssCollector
from app.analyzers.deepseek_analyzer import DeepSeekAnalyzer
from app.storage.postgres_storage import PostgresStorage
from app.storage.neo4j_storage import Neo4jStorage
from app.models.schemas import Article, AnalysisResult, StructuredNews
from app.events.clusterer import cluster
from app.embedding.news_embedder import NewsEmbedder
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
        self.embedder = NewsEmbedder()
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

                # Phase 2: 구조화 이벤트 추출 (기사당 1회 요청, fail-open)
                try:
                    structured = await self.analyzer.extract_structured(article)
                    if structured is not None:
                        article_id = self._get_article_id(article.url)
                        if article_id is not None:
                            self._save_structured_event(article_id, structured)
                except Exception as se:
                    logger.error(
                        f"Structured extraction failed for '{article.title[:50]}': {se}"
                    )

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

        # Phase 3: 이벤트 클러스터 후처리 (스케줄/배치, LLM 없음, fail-open)
        self._cluster_recent_events()

    def _cluster_recent_events(self):
        """최근 news_event_extraction 을 클러스터링하여 news_events 에 upsert.

        순수 계산(LLM 없음). 실패 시 기존 저장 그대로 유지(fail-open).
        Phase 4: 각 클러스터의 core_event_text 를 임베딩하여 embedding 저장.
        임베딩 실패 시 해당 클러스터는 저장 진행(선택적 컬럼, fail-open).
        """
        try:
            since = datetime.now() - self.config.CLUSTER_WINDOW
            rows = self.pg_storage.get_recent_event_extractions(since)
            if not rows:
                logger.debug("No recent event extractions to cluster")
                return
            clusters = cluster(rows)
            for cl in clusters:
                self.pg_storage.save_event_cluster(cl)
                self._embed_and_save(cl)
            logger.info(f"Clustered {len(rows)} extractions into {len(clusters)} events")
        except Exception as e:
            logger.error(f"Event clustering failed (fail-open): {e}")

    def _embed_and_save(self, cl):
        """core_event_text 를 임베딩하여 news_events.embedding 에 저장 (fail-open)."""
        try:
            text = cl.representative_core_event_text
            if not text:
                return
            vec = self.embedder.embed(text)
            if vec is None:
                logger.warning(
                    f"Embedding unavailable for cluster {cl.cluster_key} (skip)"
                )
                return
            self.pg_storage.update_event_embedding(cl.cluster_key, vec)
        except Exception as e:
            logger.error(
                f"Embedding save failed for cluster {cl.cluster_key} (fail-open): {e}"
            )

    def _get_article_id(self, url: str) -> Optional[int]:
        """news_analysis 저장 후 해당 행의 id 조회."""
        try:
            existing = self.pg_storage.get_analysis_by_url(url)
            if existing:
                return existing.get("id")
        except Exception as e:
            logger.error(f"Failed to fetch article id for {url}: {e}")
        return None

    def _save_structured_event(self, article_id: int, structured: StructuredNews):
        """종목명→코드 매핑 + 존재 검증 후 구조화 이벤트 저장.

        structured.stock_code는 파서가 추출한 종목명 후보. get_stock_by_name으로
        정규 매핑하고, 후보 코드가 stocks에 실제 존재하는지 검증한다. 미존재 시
        stock_code를 None으로 두고 이벤트는 저장한다(관계 무단 생성 방지).
        """
        stock_code = None
        candidate = (structured.stock_code or "").strip()
        if candidate:
            try:
                match = self.pg_storage.get_stock_by_name(candidate)
                if match and match.get("stock_code"):
                    stock_code = match["stock_code"]
            except Exception as e:
                logger.error(f"Stock mapping failed for '{candidate}': {e}")

        structured.stock_code = stock_code
        self.pg_storage.save_event_extraction(article_id, structured)

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
