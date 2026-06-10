"""
Graph Features
Extracts features from Neo4j graph relationships.
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


class GraphFeatures:
    """Features derived from graph database."""

    def get_graph_features(self, stock_code: str, storage) -> Dict:
        """Get graph-based features."""
        features = {}
        try:
            # Get sector connections
            sectors = storage.get_stock_sectors(stock_code)
            features["sector_count"] = len(sectors) if sectors else 0

            # Get theme memberships
            themes = storage.get_stock_themes(stock_code)
            features["theme_count"] = len(themes) if themes else 0

            # Get twin connections
            twins = storage.get_twin_pairs(min_correlation=0.8)
            features["twin_count"] = len(twins) if twins else 0

        except Exception as e:
            logger.debug(f"Graph features failed: {e}")
            features.update({"sector_count": 0, "theme_count": 0, "twin_count": 0})

        return features
