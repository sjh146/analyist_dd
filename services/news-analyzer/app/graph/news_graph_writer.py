"""
Neo4j News Graph Writer (Phase 7).

Upserts news-event relationships into Neo4j from ``EventCluster`` objects.
Pure graph writing -- no LLM calls. Uses MERGE for idempotency and coexists
with the existing schema (TWIN_OF, SENTIMENT_OF, etc.) without deleting
anything.
"""

import logging
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Tuple

from neo4j import GraphDatabase

from app.config import Config
from app.events.clusterer import EventCluster

logger = logging.getLogger(__name__)


class NewsGraphWriter:
    """Writes news-event relationships into Neo4j.

    ``driver`` may be injected for testing; otherwise a driver is created
    from :class:`app.config.Config`.
    """

    def __init__(self, driver=None):
        self._driver = driver
        self._owns_driver = driver is None
        if self._owns_driver:
            self._connect()

    def _connect(self):
        """Create and verify a Neo4j driver from config."""
        try:
            cfg = Config()
            self._driver = GraphDatabase.driver(
                cfg.NEO4J_URI,
                auth=(cfg.NEO4J_USER, cfg.NEO4J_PASSWORD),
            )
            self._driver.verify_connectivity()
            logger.info("Connected to Neo4j (NewsGraphWriter)")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j (NewsGraphWriter): {e}")
            self._driver = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def write_events(self, events: Iterable[EventCluster]) -> int:
        """MERGE Event nodes + (Stock)-[:HAS_EVENT]->(Event) relationships.

        Returns the number of events processed.
        """
        return self._run_batch(self._event_queries(events))

    def write_themes(self, themes: Iterable[Tuple[str, str]]) -> int:
        """MERGE Theme nodes + (Stock)-[:HAS_THEME]->(Theme).

        ``themes``: iterable of ``(stock_code, theme_name)``.
        """
        return self._run_batch(self._theme_queries(themes))

    def write_impact(self, impacts: Iterable[Dict]) -> int:
        """MERGE ImpactScore nodes + (Stock)-[:HAS_IMPACT]->(ImpactScore).

        ``impacts``: iterable of dicts with ``stock_code``, ``score``,
        ``date`` (date or str).
        """
        return self._run_batch(self._impact_queries(impacts))

    def write_co_occurs(self, pairs: Iterable[Tuple[str, str]]) -> int:
        """MERGE (Event)-[:CO_OCCURS]->(Event) co-occurrence relationships.

        ``pairs``: iterable of ``(event_id_a, event_id_b)``.
        """
        return self._run_batch(self._co_occurs_queries(pairs))

    def write_co_event(self, pairs: Iterable[Tuple[str, str]]) -> int:
        """MERGE (Stock)-[:CO_EVENT]->(Stock) shared-event relationships.

        ``pairs``: iterable of ``(stock_code_a, stock_code_b)``.
        """
        return self._run_batch(self._co_event_queries(pairs))

    def close(self):
        """Close the Neo4j driver if this instance owns it."""
        if self._owns_driver and self._driver:
            self._driver.close()
            logger.info("Neo4j connection closed (NewsGraphWriter)")

    # ------------------------------------------------------------------
    # Query builders (pure, testable)
    # ------------------------------------------------------------------
    @staticmethod
    def _event_queries(events: Iterable[EventCluster]) -> List[Tuple[str, Dict]]:
        queries = []
        for cl in events:
            params = {
                "event_id": cl.cluster_key,
                "type": cl.event_type,
                "core_event_text": cl.representative_core_event_text,
                "date": cl.event_date.isoformat(),
                "impact_score": cl.total_importance,
                "stock_code": cl.stock_code,
            }
            queries.append(
                (
                    """
                    MERGE (e:Event {event_id: $event_id})
                    SET e.type = $type,
                        e.core_event_text = $core_event_text,
                        e.date = $date,
                        e.impact_score = $impact_score
                    MERGE (s:Stock {code: $stock_code})
                    MERGE (s)-[:HAS_EVENT]->(e)
                    """,
                    params,
                )
            )
        return queries

    @staticmethod
    def _theme_queries(themes: Iterable[Tuple[str, str]]) -> List[Tuple[str, Dict]]:
        queries = []
        for stock_code, theme_name in themes:
            if not theme_name:
                continue
            queries.append(
                (
                    """
                    MERGE (t:Theme {name: $theme_name})
                    MERGE (s:Stock {code: $stock_code})
                    MERGE (s)-[:HAS_THEME]->(t)
                    """,
                    {"theme_name": theme_name, "stock_code": stock_code},
                )
            )
        return queries

    @staticmethod
    def _impact_queries(impacts: Iterable[Dict]) -> List[Tuple[str, Dict]]:
        queries = []
        for imp in impacts:
            stock_code = imp.get("stock_code")
            score = imp.get("score")
            d = imp.get("date")
            if not stock_code or score is None:
                continue
            date_str = d.isoformat() if isinstance(d, (date, datetime)) else str(d)
            queries.append(
                (
                    """
                    MERGE (i:ImpactScore {stock_code: $stock_code, date: $date})
                    SET i.score = $score
                    MERGE (s:Stock {code: $stock_code})
                    MERGE (s)-[:HAS_IMPACT]->(i)
                    """,
                    {
                        "stock_code": stock_code,
                        "score": float(score),
                        "date": date_str,
                    },
                )
            )
        return queries

    @staticmethod
    def _co_occurs_queries(pairs: Iterable[Tuple[str, str]]) -> List[Tuple[str, Dict]]:
        queries = []
        for a, b in pairs:
            if not a or not b or a == b:
                continue
            queries.append(
                (
                    """
                    MERGE (a:Event {event_id: $a})
                    MERGE (b:Event {event_id: $b})
                    MERGE (a)-[:CO_OCCURS]->(b)
                    """,
                    {"a": a, "b": b},
                )
            )
        return queries

    @staticmethod
    def _co_event_queries(pairs: Iterable[Tuple[str, str]]) -> List[Tuple[str, Dict]]:
        queries = []
        for a, b in pairs:
            if not a or not b or a == b:
                continue
            queries.append(
                (
                    """
                    MERGE (a:Stock {code: $a})
                    MERGE (b:Stock {code: $b})
                    MERGE (a)-[:CO_EVENT]->(b)
                    """,
                    {"a": a, "b": b},
                )
            )
        return queries

    # ------------------------------------------------------------------
    # Batch execution
    # ------------------------------------------------------------------
    def _run_batch(self, queries: List[Tuple[str, Dict]]) -> int:
        """Execute a batch of (cypher, params) in a single session.

        Returns the number of queries executed. Fail-open: on error the
        remaining batch is skipped and the error is logged.
        """
        if not self._driver:
            logger.warning("No Neo4j connection available (NewsGraphWriter)")
            return 0
        if not queries:
            return 0
        try:
            with self._driver.session() as session:
                for cypher, params in queries:
                    session.run(cypher, **params)
            return len(queries)
        except Exception as e:
            logger.error(f"News graph batch write failed: {e}")
            return 0
