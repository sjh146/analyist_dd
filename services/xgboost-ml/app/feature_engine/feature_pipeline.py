"""
Feature Pipeline
Orchestrates feature extraction from all data sources into a single feature dict/DataFrame.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from app.feature_engine.market_features import MarketFeatures
from app.feature_engine.company_features import CompanyFeatures
from app.feature_engine.sentiment_features import SentimentFeatures
from app.feature_engine.macro_features import MacroFeatures
from app.feature_engine.graph_features import GraphFeatures
from app.feature_engine.vector_features import VectorFeatures

logger = logging.getLogger(__name__)


class FeaturePipeline:
    """Builds complete feature sets from market, company, sentiment, macro, graph, and vector data."""

    def __init__(self, pg_conn=None, neo4j_conn=None):
        self.market = MarketFeatures()
        self.company = CompanyFeatures()
        self.sentiment = SentimentFeatures()
        self.macro = MacroFeatures()
        self.graph = GraphFeatures()
        self.vector = VectorFeatures()
        self.pg_conn = pg_conn
        self.neo4j_conn = neo4j_conn
        self._cache = {}
        self._cache_ttl = 3600

    def build_features(
        self, stock_code: str, date: str = None,
        market_df: pd.DataFrame = None,
    ) -> Dict:
        """Build complete feature set (~58 features) for a single stock on a given date."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        cache_key = f"{stock_code}:{date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        features = {}

        features.update(self.market.get_all_features(
            market_df or pd.DataFrame(), stock_code, self.pg_conn,
        ))

        features.update(self.company.get_all_features(stock_code, self.pg_conn))

        features.update(self.sentiment.get_all_features(stock_code, self.pg_conn))

        features.update(self.macro.get_all_features(self.pg_conn))

        features.update(self.graph.get_graph_features(stock_code, self.neo4j_conn))

        features.update(self.vector.get_vector_features_from_db(stock_code, self.pg_conn))

        features["feature_count"] = len(features)
        features["stock_code"] = stock_code
        features["date"] = date

        self._cache[cache_key] = features
        return features

    def build_training_features(
        self, stock_codes: List[str], start_date: str, end_date: str,
    ) -> pd.DataFrame:
        """Build feature matrix for model training across multiple stocks and dates."""
        rows = []

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        date_range = []
        current = start_dt
        while current <= end_dt:
            if current.weekday() < 5:
                date_range.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        for code in stock_codes:
            for date_str in date_range:
                try:
                    features = self.build_features(code, date_str)
                    if features.get("feature_count", 0) >= 10:
                        rows.append(features)
                except Exception as e:
                    logger.debug(f"Feature build failed for {code} {date_str}: {e}")
                    continue

        return pd.DataFrame(rows)

    def get_feature_names(self) -> List[str]:
        """Return the list of all expected feature names (for model training consistency)."""
        return sorted([
            "price", "return_1d", "return_5d", "return_20d",
            "volatility_20d", "volatility_60d",
            "ma_position_5", "ma_position_20", "ma_position_60", "ma_position_120",
            "rsi", "macd", "bb_width", "bb_position", "atr", "atr_pct",
            "stoch_k", "stoch_d",
            "volume_ratio_5", "volume_ratio_20",
            "foreign_net_buy", "foreign_net_buy_5d",
            "institution_net_buy", "institution_net_buy_5d",
            "basis", "basis_change_5d",
            "revenue", "operating_profit", "net_income",
            "op_margin", "net_margin",
            "per_current", "pbr_current", "roe", "debt_ratio",
            "revenue_growth_yoy", "op_margin_change_yoy",
            "per_percentile", "pbr_percentile",
            "sentiment_avg", "sentiment_avg_5d", "sentiment_avg_20d",
            "sentiment_trend", "sentiment_volatility",
            "news_count_5d", "news_count_20d", "disclosure_count_5d",
            "authenticity_avg", "positive_ratio", "negative_ratio",
            "interest_rate", "interest_rate_change_1m", "interest_rate_change_3m",
            "fx_usd_krw", "fx_change_1m", "fx_change_3m",
            "oil_wti", "oil_change_1m", "oil_change_3m",
            "cpi_yoy", "ppi_yoy", "yield_spread", "credit_spread",
            "sector_count", "theme_count", "theme_max_relevance",
            "twin_count", "twin_avg_correlation",
            "cycle_up", "cycle_down",
            "avg_similarity_top10", "max_similarity", "similarity_std",
            "similar_count", "similar_stocks_return_avg", "similar_stocks_return_std",
        ])

    def set_db_connections(self, pg_conn=None, neo4j_conn=None):
        """Set or update database connections."""
        self.pg_conn = pg_conn
        self.neo4j_conn = neo4j_conn

    def clear_cache(self):
        """Clear the feature cache."""
        self._cache.clear()
