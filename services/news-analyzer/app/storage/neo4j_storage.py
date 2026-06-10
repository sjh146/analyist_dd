"""
Neo4j Storage for News/SNS Analyzer
Stores sentiment relationships in the graph database.
"""

import logging
from datetime import datetime
from neo4j import GraphDatabase
from app.config import Config

logger = logging.getLogger(__name__)


class Neo4jStorage:
    def __init__(self):
        self.config = Config()
        self._driver = None
        self._connect()

    def _connect(self):
        """Connect to Neo4j."""
        try:
            self._driver = GraphDatabase.driver(
                self.config.NEO4J_URI,
                auth=(self.config.NEO4J_USER, self.config.NEO4J_PASSWORD),
            )
            self._driver.verify_connectivity()
            logger.info("Connected to Neo4j")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")

    def save_sentiment_relationship(
        self, stock_code: str, sentiment_score: float, date: datetime
    ):
        """Create or update SENTIMENT_OF relationship in Neo4j."""
        if not self._driver:
            logger.warning("No Neo4j connection available")
            return

        try:
            with self._driver.session() as session:
                session.run(
                    """
                    MERGE (s:Stock {code: $stock_code})
                    MERGE (sen:Sentiment {date: $date_str})
                    MERGE (sen)-[r:SENTIMENT_OF]->(s)
                    SET r.score = $score,
                        r.updated_at = $updated_at
                    """,
                    stock_code=stock_code,
                    date_str=date.strftime("%Y-%m-%d"),
                    score=sentiment_score,
                    updated_at=datetime.now().isoformat(),
                )
        except Exception as e:
            logger.error(f"Failed to save Neo4j sentiment: {e}")

    def close(self):
        """Close Neo4j connection."""
        if self._driver:
            self._driver.close()
            logger.info("Neo4j connection closed")
