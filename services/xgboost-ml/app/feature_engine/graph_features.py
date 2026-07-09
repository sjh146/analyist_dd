"""
Graph Features
Extracts features from Neo4j graph relationships.
"""

import logging
import numpy as np
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class GraphFeatures:
    """Features derived from Neo4j graph: sector, theme, twin, cycle relationships."""

    def get_graph_features(self, stock_code: str, neo4j_conn=None) -> Dict:
        """Get all graph-based features from Neo4j."""
        features = {
            "sector_count": 0, "theme_count": 0,
            "theme_max_relevance": 0.0, "twin_count": 0,
            "twin_avg_correlation": 0.0,
            "cycle_up": 0, "cycle_down": 0,
            "sector_momentum": 0.0, "theme_momentum": 0.0,
        }

        if neo4j_conn is None:
            return features

        try:
            features.update(self._get_sector_features(stock_code, neo4j_conn))
            features.update(self._get_theme_features(stock_code, neo4j_conn))
            features.update(self._get_twin_features(stock_code, neo4j_conn))
            features.update(self._get_cycle_features(stock_code, neo4j_conn))
        except Exception as e:
            logger.debug(f"Graph features failed for {stock_code}: {e}")

        return features

    def _get_sector_features(self, stock_code: str, driver) -> Dict:
        """Count sector relationships."""
        features = {"sector_count": 0, "sector_momentum": 0.0}
        try:
            result = driver.run("""
                MATCH (s:Stock {code: $code})-[:BELONGS_TO]->(sec:Sector)
                RETURN count(sec) as cnt
            """, code=stock_code)
            record = result.single()
            if record:
                features["sector_count"] = record["cnt"]
        except Exception:
            pass
        return features

    def _get_theme_features(self, stock_code: str, driver) -> Dict:
        """Count theme memberships and max relevance."""
        features = {"theme_count": 0, "theme_max_relevance": 0.0, "theme_momentum": 0.0}
        try:
            result = driver.run("""
                MATCH (s:Stock {code: $code})-[r:PART_OF_THEME]->(t:Theme)
                RETURN count(t) as cnt, max(r.relevance) as max_rel
            """, code=stock_code)
            record = result.single()
            if record:
                features["theme_count"] = record["cnt"] if record["cnt"] else 0
                features["theme_max_relevance"] = float(record["max_rel"]) if record["max_rel"] else 0.0
        except Exception:
            pass
        return features

    def _get_twin_features(self, stock_code: str, driver) -> Dict:
        """Count twin pairs and average correlation."""
        features = {"twin_count": 0, "twin_avg_correlation": 0.0}
        try:
            result = driver.run("""
                MATCH (s:Stock {code: $code})-[r:TWIN_OF]-(:Stock)
                RETURN count(r) as cnt, avg(r.correlation) as avg_corr
            """, code=stock_code)
            record = result.single()
            if record:
                features["twin_count"] = record["cnt"] if record["cnt"] else 0
                features["twin_avg_correlation"] = float(record["avg_corr"]) if record["avg_corr"] else 0.0
        except Exception:
            pass
        return features

    def _get_cycle_features(self, stock_code: str, driver) -> Dict:
        """Get cycle phase features (one-hot encoded)."""
        features = {"cycle_up": 0, "cycle_down": 0}
        try:
            result = driver.run("""
                MATCH (s:Stock {code: $code})-[r:FOLLOWS_CYCLE]->(c:Cycle)
                RETURN r.phase as phase
            """, code=stock_code)
            for record in result:
                phase = record["phase"]
                if phase == "up" or phase == "recovery":
                    features["cycle_up"] = 1
                elif phase == "down" or phase == "downturn":
                    features["cycle_down"] = 1
        except Exception:
            pass
        return features
