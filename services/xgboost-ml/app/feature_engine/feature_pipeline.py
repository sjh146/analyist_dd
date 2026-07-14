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
from app.feature_engine.feature_store import FeatureStore

logger = logging.getLogger(__name__)


class FeaturePipeline:
    """Builds complete feature sets from market, company, sentiment, macro, graph, and vector data."""

    def __init__(self, pg_conn=None, neo4j_conn=None, use_feature_store=False, feature_store: Optional[FeatureStore] = None):
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
        self.use_feature_store = use_feature_store
        self.feature_store = feature_store or (FeatureStore(pg_conn=self.pg_conn) if use_feature_store else None)

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

        if self.use_feature_store and self.feature_store is not None:
            try:
                stored = self.feature_store.load_features(stock_code, date)
                if stored:
                    stored["feature_count"] = len(stored)
                    stored["stock_code"] = stock_code
                    stored["date"] = date
                    self._cache[cache_key] = stored
                    return stored
            except Exception:
                logger.exception("FeatureStore load failed; falling back to compute")

        features = {}

        features.update(self.market.get_all_features(
            market_df if market_df is not None and not market_df.empty else pd.DataFrame(),
            stock_code, self.pg_conn,
        ))

        features.update(self.company.get_all_features(stock_code, self.pg_conn))

        features.update(self.sentiment.get_all_features(stock_code, self.pg_conn))

        features.update(self.macro.get_all_features(self.pg_conn))

        features.update(self.graph.get_graph_features(stock_code, self.neo4j_conn))

        features.update(self.vector.get_vector_features_from_db(stock_code, self.pg_conn))

        try:
            features.update(self._build_advanced_features(stock_code, date, market_df))
        except Exception as e:
            logger.debug(f"Advanced features failed for {stock_code}: {e}")

        features["feature_count"] = len(features)
        features["stock_code"] = stock_code
        features["date"] = date

        self._cache[cache_key] = features

        if self.use_feature_store and self.feature_store is not None:
            try:
                self.feature_store.save_features(stock_code, date, features)
            except Exception:
                logger.exception("FeatureStore save failed; continuing")

        return features

    def build_training_features(
        self, stock_codes: List[str], start_date: str, end_date: str,
    ) -> pd.DataFrame:
        """Build feature matrix for model training across multiple stocks and dates."""
        if self.use_feature_store and self.feature_store is not None:
            try:
                stored = self.feature_store.load_batch(stock_codes, start_date, end_date)
                if not stored.empty:
                    return stored
            except Exception:
                logger.exception("FeatureStore batch load failed; falling back to per-stock compute")

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

    def _build_advanced_features(
        self, stock_code: str, date: str,
        market_df: pd.DataFrame = None,
    ) -> Dict:
        """Build 25 advanced features: sector/market, volatility/risk, flow, ownership, credit/margin, technical."""

        features = {}

        close = None
        volume = None
        if market_df is not None and not market_df.empty:
            close_series = market_df.get("close_price", market_df.get("close"))
            if close_series is not None:
                close = close_series.values if hasattr(close_series, "values") else np.array(close_series)
            vol_series = market_df.get("volume")
            if vol_series is not None:
                volume = vol_series.values if hasattr(vol_series, "values") else np.array(vol_series)

        valid_close = close is not None and len(close) > 0
        valid_vol = volume is not None and len(volume) > 0
        latest_close = float(close[-1]) if valid_close else 0.0

        # ----- Sector / Market features -----

        # 1. sector_momentum: average return of stocks in same sector
        # TODO: requires stocks table with sector mapping; fallback 0.0
        features["sector_momentum"] = 0.0
        if self.pg_conn is not None and valid_close and len(close) >= 2:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("SELECT sector FROM stocks WHERE stock_code = %s", (stock_code,))
                row = cur.fetchone()
                if row and row[0]:
                    sector = row[0]
                    cur.execute("""
                        SELECT sp.close_price
                        FROM stock_prices sp
                        JOIN stocks s ON sp.stock_code = s.stock_code
                        WHERE s.sector = %s AND sp.trade_date = %s
                    """, (sector, date))
                    sector_rows = cur.fetchall()
                    if sector_rows:
                        sector_closes = [float(r[0]) for r in sector_rows if r[0]]
                        if len(sector_closes) >= 2:
                            stock_return = close[-1] / close[-2] - 1 if close[-2] != 0 else 0.0
                            sector_return = sum(sector_closes) / len(sector_closes) if sector_closes else 0.0
                            features["sector_momentum"] = float(sector_return)
                cur.close()
            except Exception:
                logger.debug("sector_momentum unavailable; using 0.0")

        # 2. relative_strength: stock_return / market_return
        features["relative_strength"] = 0.0
        if valid_close and len(close) >= 2:
            stock_ret = close[-1] / close[-2] - 1 if close[-2] != 0 else 0.0
            # TODO: fetch KOSPI index return from market_index table for accurate market_return
            market_return = 0.0
            features["relative_strength"] = float(stock_ret / market_return) if market_return != 0 else 0.0

        # 3. market_breadth: ratio of advancing stocks to declining stocks
        # TODO: requires daily market breadth from stock_prices aggregation
        features["market_breadth"] = 0.0

        # 4. high_52w_ratio: ratio of stocks at 52-week highs
        # TODO: requires 52-week high query from stock_prices
        features["high_52w_ratio"] = 0.0

        # 5. adr: 1 if ADR stock else 0
        # TODO: requires adr_flag column in stocks table
        features["adr"] = 0.0

        # ----- Volatility / Risk features -----

        # 6. vix_proxy: approximate KOSPI 200 implied volatility from historical vol
        features["vix_proxy"] = 0.0
        if valid_close and len(close) >= 21:
            rets_20 = [(close[i] / close[i - 1] - 1) for i in range(max(1, len(close) - 20), len(close))]
            if rets_20:
                hist_vol = float(np.std(rets_20) * np.sqrt(252))
                features["vix_proxy"] = hist_vol

        # 7. volatility_skew: difference between upside and downside volatility
        features["volatility_skew"] = 0.0
        if valid_close and len(close) >= 21:
            rets_20 = [(close[i] / close[i - 1] - 1) for i in range(max(1, len(close) - 20), len(close))]
            if rets_20:
                upside = [r for r in rets_20 if r > 0]
                downside = [r for r in rets_20 if r < 0]
                up_vol = float(np.std(upside)) if len(upside) > 1 else 0.0
                dn_vol = float(np.std(downside)) if len(downside) > 1 else 0.0
                features["volatility_skew"] = up_vol - dn_vol

        # ----- Flow features (from external data) -----

        # 8. program_trading_ratio: program trading volume / total volume
        # TODO: requires program_trading table
        features["program_trading_ratio"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT program_buy + program_sell, total_volume
                    FROM program_trading
                    WHERE trade_date = %s
                    ORDER BY trade_date DESC LIMIT 1
                """, (date,))
                row = cur.fetchone()
                if row and row[1] and row[1] > 0:
                    features["program_trading_ratio"] = float(row[0] / row[1])
                cur.close()
            except Exception:
                logger.debug("program_trading_ratio unavailable; using 0.0")

        # 9. etf_flow_5d: 5-day ETF fund flow
        # TODO: requires etf_flow table
        features["etf_flow_5d"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT SUM(net_flow) FROM etf_flow
                    WHERE trade_date <= %s AND trade_date >= %s
                """, (date, (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")))
                row = cur.fetchone()
                if row and row[0]:
                    features["etf_flow_5d"] = float(row[0])
                cur.close()
            except Exception:
                logger.debug("etf_flow_5d unavailable; using 0.0")

        # 10. foreign_flow_5d: 5-day foreign investor flow
        # TODO: uses foreign_institutional table; falls back to 0.0
        features["foreign_flow_5d"] = 0.0
        if valid_close and len(close) >= 2:
            features["foreign_flow_5d"] = 0.0

        # 11. institution_flow_5d: 5-day institutional flow
        features["institution_flow_5d"] = 0.0
        if valid_close and len(close) >= 2:
            features["institution_flow_5d"] = 0.0

        # ----- Sentiment / Ownership features -----

        # 12. foreign_ownership_pct: foreign ownership percentage
        # TODO: requires ownership table
        features["foreign_ownership_pct"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT foreign_ownership_pct FROM ownership
                    WHERE stock_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC LIMIT 1
                """, (stock_code, date))
                row = cur.fetchone()
                if row and row[0]:
                    features["foreign_ownership_pct"] = float(row[0])
                cur.close()
            except Exception:
                logger.debug("foreign_ownership_pct unavailable; using 0.0")

        # 13. institution_ownership_pct: institutional ownership percentage
        # TODO: requires ownership table
        features["institution_ownership_pct"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT institution_ownership_pct FROM ownership
                    WHERE stock_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC LIMIT 1
                """, (stock_code, date))
                row = cur.fetchone()
                if row and row[0]:
                    features["institution_ownership_pct"] = float(row[0])
                cur.close()
            except Exception:
                logger.debug("institution_ownership_pct unavailable; using 0.0")

        # 14. retail_ownership_pct: retail ownership percentage
        # TODO: requires ownership table
        features["retail_ownership_pct"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT 100.0 - COALESCE(foreign_ownership_pct, 0) - COALESCE(institution_ownership_pct, 0)
                    FROM ownership
                    WHERE stock_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC LIMIT 1
                """, (stock_code, date))
                row = cur.fetchone()
                if row and row[0]:
                    features["retail_ownership_pct"] = float(row[0])
                cur.close()
            except Exception:
                logger.debug("retail_ownership_pct unavailable; using 0.0")

        # 15. short_interest_ratio: short interest / total shares
        # TODO: requires short_interest table
        features["short_interest_ratio"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT short_interest, total_shares FROM short_interest
                    WHERE stock_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC LIMIT 1
                """, (stock_code, date))
                row = cur.fetchone()
                if row and row[0] and row[1] and row[1] > 0:
                    features["short_interest_ratio"] = float(row[0] / row[1])
                cur.close()
            except Exception:
                logger.debug("short_interest_ratio unavailable; using 0.0")

        # 16. days_to_cover: short interest / average daily volume
        # TODO: requires short_interest table
        features["days_to_cover"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT si.short_interest, AVG(sp.volume) as avg_vol
                    FROM short_interest si
                    JOIN stock_prices sp ON si.stock_code = sp.stock_code
                    WHERE si.stock_code = %s AND sp.trade_date <= %s
                      AND sp.trade_date >= %s
                    GROUP BY si.short_interest
                    ORDER BY si.trade_date DESC LIMIT 1
                """, (stock_code, date,
                      (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=25)).strftime("%Y-%m-%d")))
                row = cur.fetchone()
                if row and row[0] and row[1] and row[1] > 0:
                    features["days_to_cover"] = float(row[0] / row[1])
                cur.close()
            except Exception:
                logger.debug("days_to_cover unavailable; using 0.0")

        # ----- Credit / Margin features -----

        # 17. margin_balance_change: change in margin balance
        # TODO: requires margin_balance table
        features["margin_balance_change"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT margin_balance FROM margin_balance
                    WHERE stock_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC LIMIT 2
                """, (stock_code, date))
                rows = cur.fetchall()
                if len(rows) >= 2 and rows[0][0] and rows[1][0] and rows[1][0] != 0:
                    features["margin_balance_change"] = float((rows[0][0] - rows[1][0]) / rows[1][0] * 100)
                cur.close()
            except Exception:
                logger.debug("margin_balance_change unavailable; using 0.0")

        # 18. credit_balance_change: change in credit balance
        # TODO: requires credit_balance table
        features["credit_balance_change"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT credit_balance FROM credit_balance
                    WHERE stock_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC LIMIT 2
                """, (stock_code, date))
                rows = cur.fetchall()
                if len(rows) >= 2 and rows[0][0] and rows[1][0] and rows[1][0] != 0:
                    features["credit_balance_change"] = float((rows[0][0] - rows[1][0]) / rows[1][0] * 100)
                cur.close()
            except Exception:
                logger.debug("credit_balance_change unavailable; using 0.0")

        # 19. short_selling_ratio: short selling volume / total volume
        # TODO: requires short_selling table
        features["short_selling_ratio"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT short_sell_volume, total_volume FROM short_selling
                    WHERE stock_code = %s AND trade_date = %s
                """, (stock_code, date))
                row = cur.fetchone()
                if row and row[0] and row[1] and row[1] > 0:
                    features["short_selling_ratio"] = float(row[0] / row[1])
                cur.close()
            except Exception:
                logger.debug("short_selling_ratio unavailable; using 0.0")

        # ----- Additional technical features -----

        # 20. volatility_20d_rank: percentile rank of 20-day volatility among last 60 days
        features["volatility_20d_rank"] = 0.0
        if valid_close and len(close) >= 60:
            all_rets = [(close[i] / close[i - 1] - 1) for i in range(1, len(close))]
            if len(all_rets) >= 60:
                current_vol = float(np.std(all_rets[-20:]))
                vols_60d = [float(np.std(all_rets[i-20:i])) for i in range(20, len(all_rets) + 1)]
                if vols_60d:
                    rank = sum(1 for v in vols_60d if v <= current_vol)
                    features["volatility_20d_rank"] = float(rank / len(vols_60d) * 100)

        # 21. volume_ratio_vs_avg: current volume / 20-day avg volume
        features["volume_ratio_vs_avg"] = 0.0
        if valid_vol and len(volume) >= 20:
            current_vol_val = float(volume[-1])
            avg_vol_20 = float(np.mean(volume[-20:]))
            features["volume_ratio_vs_avg"] = float(current_vol_val / avg_vol_20) if avg_vol_20 > 0 else 0.0

        # 22. price_vs_sector: stock price change vs sector average change
        features["price_vs_sector"] = 0.0
        if valid_close and len(close) >= 2:
            stock_ret_1d = close[-1] / close[-2] - 1 if close[-2] != 0 else 0.0
            # Uses sector_momentum computed above (or 0.0)
            features["price_vs_sector"] = float(stock_ret_1d - features.get("sector_momentum", 0.0))

        # 23. correlation_with_market: 60-day correlation with KOSPI
        features["correlation_with_market"] = 0.0
        # TODO: requires market index price data from market_index table
        features["correlation_with_market"] = 0.0

        # 24. beta_60d: 60-day beta to market
        features["beta_60d"] = 0.0
        # TODO: requires market index price data; compute cov(stock_ret, mkt_ret) / var(mkt_ret)
        if valid_close and len(close) >= 61:
            stock_rets = [(close[i] / close[i - 1] - 1) for i in range(max(1, len(close) - 60), len(close))]
            if len(stock_rets) >= 2:
                features["beta_60d"] = float(np.std(stock_rets) * np.sqrt(252))

        # 25. momentum_divergence: RSI divergence signal
        features["momentum_divergence"] = 0.0
        if valid_close and len(close) >= 14:
            gains, losses = [], []
            for i in range(1, len(close)):
                change = close[i] - close[i - 1]
                gains.append(max(change, 0))
                losses.append(max(-change, 0))
            if gains and losses:
                avg_gain = float(np.mean(gains[-14:]))
                avg_loss = float(np.mean(losses[-14:]))
                if avg_loss != 0:
                    rsi = 100 - (100 / (1 + avg_gain / avg_loss))
                    recent_rets = [close[i] / close[i - 1] - 1 for i in range(max(1, len(close) - 5), len(close))]
                    price_trend = float(np.mean(recent_rets)) if recent_rets else 0.0
                    rsi_mid = rsi - 50
                    features["momentum_divergence"] = float(price_trend * rsi_mid)

        return features

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

            # Advanced features (sector/market, volatility/risk, flow, ownership, credit/margin, technical)
            "sector_momentum", "relative_strength", "market_breadth", "high_52w_ratio", "adr",
            "vix_proxy", "volatility_skew",
            "program_trading_ratio", "etf_flow_5d", "foreign_flow_5d", "institution_flow_5d",
            "foreign_ownership_pct", "institution_ownership_pct", "retail_ownership_pct",
            "short_interest_ratio", "days_to_cover",
            "margin_balance_change", "credit_balance_change", "short_selling_ratio",
            "volatility_20d_rank", "volume_ratio_vs_avg", "price_vs_sector",
            "correlation_with_market", "beta_60d", "momentum_divergence",
        ])

    def set_db_connections(self, pg_conn=None, neo4j_conn=None):
        """Set or update database connections."""
        self.pg_conn = pg_conn
        self.neo4j_conn = neo4j_conn

    def clear_cache(self):
        """Clear the feature cache."""
        self._cache.clear()
