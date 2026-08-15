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
from typing import List, Dict, Optional, Tuple

from app.config import Config
from app.collectors.rss_collector import RssCollector
from app.analyzers.deepseek_analyzer import DeepSeekAnalyzer
from app.storage.postgres_storage import PostgresStorage
from app.storage.neo4j_storage import Neo4jStorage
from app.models.schemas import Article, AnalysisResult, StructuredNews
from app.events.clusterer import cluster, EventCluster
from app.graph.news_graph_writer import NewsGraphWriter
from app.embedding.news_embedder import NewsEmbedder
from app.data_quality_integration import DataQualityIntegration
from app.metrics_integration import (
    init_metrics, on_article_collected, on_article_analyzed, sentiment_analysis_total,
    on_articles_collected, on_extraction_saved, on_cluster_saved, on_embedding_saved,
)

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
        self.news_graph_writer = NewsGraphWriter()
        self.dq_integration = DataQualityIntegration(
            db_conn_provider=self.pg_storage._get_conn,
            db_conn_putter=self.pg_storage._put_conn,
        )
        self.embedder = NewsEmbedder()
        self._validated_at_ready = self.pg_storage._ensure_validated_at_column()
        init_metrics(9101)
        self._backfill_sentiment_metrics()
        self._running = False

    def _backfill_sentiment_metrics(self):
        """One-time backfill of sentiment metrics from existing DB records."""
        conn = self.pg_storage._get_conn()
        if not conn:
            logger.warning("No DB connection for sentiment metric backfill")
            return
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(sentiment_label, 'unknown'), count(*) "
                "FROM news_analysis GROUP BY sentiment_label"
            )
            rows = cur.fetchall()
            cur.close()
            for label, cnt in rows:
                sentiment_analysis_total.labels(
                    source="deepseek", sentiment=label
                ).inc(cnt)
                logger.info(f"Backfilled sentiment metric: {label}={cnt}")
        except Exception as e:
            logger.warning(f"Could not backfill sentiment metrics: {e}")
        finally:
            if conn:
                self.pg_storage._put_conn(conn)

    async def analyze_recent_articles(self):
        """Collect and analyze recent articles from all sources."""
        logger.info("Starting article collection and analysis...")

        # Step 1: Collect articles
        articles = await self.collector.collect_all()
        logger.info(f"Collected {len(articles)} articles")
        on_articles_collected(len(articles))  # post-dedup 수집량
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
                on_cluster_saved()
                self._embed_and_save(cl)
            logger.info(f"Clustered {len(rows)} extractions into {len(clusters)} events")
            # Phase 7: Neo4j 관계 upsert (LLM 없음, fail-open)
            self._write_news_graph(clusters)
        except Exception as e:
            logger.error(f"Event clustering failed (fail-open): {e}")

    def _write_news_graph(self, clusters: List[EventCluster]):
        """클러스터 기반 Neo4j 관계 upsert (Phase 7).

        Event/Theme/ImpactScore 노드 + HAS_EVENT/HAS_THEME/HAS_IMPACT,
        CO_OCCURS(동시발생), CO_EVENT(공동 이벤트) 관계를 MERGE 로 기록.
        순수 그래프 쓰기(LLM 없음). 실패 시 기존 저장 그대로 유지(fail-open).
        """
        try:
            # stock_code가 없는 클러스터(종목 매칭 실패 기사)는 그래프에 넣지 않는다
            # — MERGE (:Stock {code: null}) SemanticError 방지 (2026-08 실측).
            clusters = [cl for cl in clusters if cl.stock_code]
            if not clusters:
                logger.debug("No stock-resolved clusters for news graph")
                return
            self.news_graph_writer.write_events(clusters)
            # Theme: 각 클러스터의 event_type 을 테마로 취급 (taxonomy 기반)
            themes = [
                (cl.stock_code, cl.event_type)
                for cl in clusters
                if cl.event_type
            ]
            self.news_graph_writer.write_themes(themes)
            # ImpactScore: 종목별 일자별 총 중요도
            impacts = [
                {
                    "stock_code": cl.stock_code,
                    "score": cl.total_importance,
                    "date": cl.event_date,
                }
                for cl in clusters
            ]
            self.news_graph_writer.write_impact(impacts)
            # CO_OCCURS: 같은 종목·같은 일자 클러스터 간 동시발생
            co_occurs = self._co_occur_pairs(clusters)
            self.news_graph_writer.write_co_occurs(co_occurs)
            # CO_EVENT: 같은 일자·같은 이벤트 타입을 공유하는 종목 쌍
            co_event = self._co_event_pairs(clusters)
            self.news_graph_writer.write_co_event(co_event)
            logger.info(
                f"News graph written: {len(clusters)} events, "
                f"{len(themes)} themes, {len(impacts)} impacts, "
                f"{len(co_occurs)} co-occur, {len(co_event)} co-event"
            )
        except Exception as e:
            logger.error(f"News graph write failed (fail-open): {e}")

    @staticmethod
    def _co_occur_pairs(clusters: List[EventCluster]) -> List[Tuple[str, str]]:
        """같은 종목·같은 일자 클러스터 간 (event_id, event_id) 쌍."""
        pairs = []
        by_key: Dict[Tuple[str, str], List[str]] = {}
        for cl in clusters:
            by_key.setdefault((cl.stock_code, cl.event_date), []).append(cl.cluster_key)
        for ids in by_key.values():
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    pairs.append((ids[i], ids[j]))
        return pairs

    @staticmethod
    def _co_event_pairs(clusters: List[EventCluster]) -> List[Tuple[str, str]]:
        """같은 일자·같은 이벤트 타입을 공유하는 종목 코드 쌍."""
        pairs = []
        by_key: Dict[Tuple[str, str], List[str]] = {}
        for cl in clusters:
            by_key.setdefault((cl.event_date, cl.event_type), []).append(cl.stock_code)
        for codes in by_key.values():
            uniq = sorted({c for c in set(codes) if c})
            for i in range(len(uniq)):
                for j in range(i + 1, len(uniq)):
                    pairs.append((uniq[i], uniq[j]))
        return pairs

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
            on_embedding_saved()
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
        on_extraction_saved(event_type=structured.event_type or "기타")

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
