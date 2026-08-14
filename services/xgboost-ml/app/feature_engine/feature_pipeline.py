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
from app.feature_engine.factor_features import FactorFeatures
from app.feature_engine.scorer import QualityScorer
from app.feature_engine.kalman_filter import KalmanFeatureFilter
from app.feature_engine.bayes_factor_features import BayesFactorFeatures
from app.feature_engine.news_event_features import NewsEventFeatures

logger = logging.getLogger(__name__)


class FeaturePipeline:
    """Builds complete feature sets from market, company, sentiment, macro, graph, and vector data."""

    def __init__(self, pg_conn=None, neo4j_conn=None, use_feature_store=False, feature_store: Optional[FeatureStore] = None):
        self.market = MarketFeatures()
        self.factors = FactorFeatures()
        self.company = CompanyFeatures()
        self.sentiment = SentimentFeatures()
        self.macro = MacroFeatures()
        self.graph = GraphFeatures()
        self.vector = VectorFeatures()
        self.scorer = QualityScorer()
        self.kalman = KalmanFeatureFilter()
        self.bayes_factors = BayesFactorFeatures()
        self.news_events = NewsEventFeatures()
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

        # Load market data from DB if not provided
        if market_df is None or market_df.empty:
            if self.pg_conn is not None:
                try:
                    cur = self.pg_conn.cursor()
                    cur.execute("""
                        SELECT trade_date, open_price, high_price, low_price, close_price, volume
                        FROM market_data
                        WHERE stock_code = %s AND trade_date <= %s
                        ORDER BY trade_date DESC
                        LIMIT 250
                    """, (stock_code, date))
                    rows = cur.fetchall()
                    cur.close()
                    if rows:
                        market_df = pd.DataFrame(rows, columns=['trade_date', 'open', 'high', 'low', 'close', 'volume'])
                        market_df = market_df.sort_values('trade_date').reset_index(drop=True)
                except Exception as e:
                    logger.debug(f"Failed to load market data for {stock_code}: {e}")
                    self.pg_conn.rollback()
                    market_df = pd.DataFrame()

        # Filter market_df to current date only (prevent look-ahead bias / stale price)
        if market_df is not None and not market_df.empty and date is not None:
            if 'trade_date' in market_df.columns:
                market_df = market_df.copy()
                mkt_dates = pd.to_datetime(market_df['trade_date'])
                cutoff = pd.Timestamp(date)
                market_df = market_df[mkt_dates <= cutoff].reset_index(drop=True)

        features.update(self.market.get_all_features(
            market_df if market_df is not None and not market_df.empty else pd.DataFrame(),
            stock_code, self.pg_conn,
        ))

        # Kalman filter features (denoised momentum)
        if market_df is not None and not market_df.empty:
            close_s = market_df.get("close_price", market_df.get("close"))
            if close_s is not None and len(close_s) > 0:
                close_arr = close_s.values if hasattr(close_s, 'values') else np.array(close_s)
                features.update(self.kalman.smooth_returns(close_arr))

        # Bayesian momentum/factor features (parallel path, A/B against Kalman)
        if market_df is not None and not market_df.empty:
            close_s = market_df.get("close_price", market_df.get("close"))
            if close_s is not None and len(close_s) > 0:
                close_arr = close_s.values if hasattr(close_s, 'values') else np.array(close_s)
                features.update(self.bayes_factors.compute(close_arr))

        features.update(self.factors.get_all_factors(stock_code, market_df, self.pg_conn))

        features.update(self.company.get_all_features(stock_code, self.pg_conn))

        features.update(self.sentiment.get_all_features(stock_code, self.pg_conn))

        # News event features (market impact, event taxonomy, theme exposure)
        features.update(self.news_events.get_all_features(stock_code, self.pg_conn))

        # Real sentiment from stock_sentiment table
        sentiment = self._get_stock_sentiment(stock_code, date)
        features.update(sentiment)

        # Quality score (F-Score from financial data, 0~1)
        features["quality_score"] = self.scorer.get_f_score(stock_code, self.pg_conn)

        features.update(self.macro.get_all_features(self.pg_conn))

        # Economic event features
        economic = self._get_economic_events(date)
        features.update(economic)

        features.update(self.graph.get_graph_features(stock_code, self.neo4j_conn))

        features.update(self.vector.get_vector_features_from_db(stock_code, self.pg_conn))

        try:
            features.update(self._build_advanced_features(stock_code, date, market_df))
        except Exception as e:
            logger.debug(f"Advanced features failed for {stock_code}: {e}")

        # Trainer-consistent engineered features (interactions / rolling means /
        # target MAs). Without them the champion receives 0.0 fills and
        # collapses to a constant prediction (Aug 2026 regression). The
        # cross-sectional rank_* features are injected later via
        # compute_cross_sectional_ranks() once the full universe is built.
        try:
            self._build_trainer_consistent_features(features, market_df)
        except Exception as e:
            logger.debug(f"Trainer-consistent features failed for {stock_code}: {e}")

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

    def _build_trainer_consistent_features(
        self, features: Dict, market_df: pd.DataFrame = None,
    ) -> None:
        """Replicate the engineered features ``Trainer.prepare_training_data``
        adds (trainer.py:67-134) so INFERENCE matches TRAINING.

        Without these, the champion model receives 0.0 for ~20 of its 62
        features at inference and collapses to a near-constant prediction
        (hit Aug 2026: every KOSDAQ stock predicted DOWN, up:0/down:1668).

        This method covers the per-row pieces (interactions, rolling means,
        target MAs). The cross-sectional ``rank_*`` features are injected by
        ``compute_cross_sectional_ranks`` once the whole universe is built
        (they need the per-date cross-section, see swing_screener main()).

        Mutates ``features`` in place. Failures degrade to the existing values
        (0.0 fills) rather than crashing the pipeline.
        """
        # 1. Interaction pairs — identical formulas to trainer.py:71-80.
        interaction_pairs = [
            ("return_1d", "volatility_20d", "momentum_vs_volatility"),
            ("return_5d", "return_20d", "trend_interaction"),
            ("volume_ratio_5", "return_5d", "volume_price_trend"),
            ("ma_position_5", "ma_position_20", "cross_trend"),
            ("volatility_20d", "volume_ratio_5", "volatility_volume"),
            ("return_1d", "return_5d", "short_medium_term_momentum"),
            ("return_5d", "ma_position_20", "trend_confirmation"),
            ("price", "volume_ratio_5", "price_volume"),
        ]
        for a, b, name in interaction_pairs:
            fa = float(features.get(a, 0.0) or 0.0)
            fb = float(features.get(b, 0.0) or 0.0)
            features[name] = fa * fb

        # 2. Per-stock rolling means + target MA — computed from the market
        #    series with the SAME definitions as MarketFeatures so the values
        #    equal what the training df contained (trainer.py:87-131).
        if market_df is None or market_df.empty:
            return
        close_s = market_df.get("close_price", market_df.get("close"))
        if close_s is None or len(close_s) < 5:
            return
        try:
            close = pd.Series(
                [float(c) for c in close_s], dtype=np.float64
            ).reset_index(drop=True)
            rets = close.pct_change()  # return_1d series (first row NaN -> 0)
            ret_5d = close.pct_change(5)
            ret_20d = close.pct_change(20)
            # np.std (ddof=0) matches MarketFeatures.volatility_20d.
            vol20 = rets.rolling(20, min_periods=2).std(ddof=0)

            vol_s = market_df.get("volume")
            volr5 = None
            if vol_s is not None and len(vol_s) >= 5:
                vol = pd.Series([float(v) for v in vol_s], dtype=np.float64).reset_index(drop=True)
                volr5 = vol / vol.rolling(5, min_periods=1).mean()

            def _last(s: pd.Series, default: float = 0.0) -> float:
                v = s.iloc[-1] if len(s) > 0 else default
                return float(v) if pd.notna(v) else default

            features["return_5d_mean_10d"] = _last(ret_5d.rolling(10, min_periods=1).mean())
            features["volatility_20d_mean_10d"] = _last(vol20.rolling(10, min_periods=1).mean())
            features["volume_ratio_5_mean_10d"] = (
                _last(volr5.rolling(10, min_periods=1).mean()) if volr5 is not None else 0.0
            )
            features["target_ma_5"] = _last(rets.rolling(5, min_periods=1).mean())
            features["target_ma_10"] = _last(rets.rolling(10, min_periods=1).mean())
            features["target_ma_20"] = _last(rets.rolling(20, min_periods=1).mean())
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"trainer-consistent rolling features unavailable: {e}")

    def compute_cross_sectional_ranks(self, features_by_code: Dict[str, Dict]) -> None:
        """Inject ``rank_*`` features — percentile rank within each date's
        cross-section — replicating ``Trainer.prepare_training_data``
        (trainer.py:107-118). Mutates the feature dicts in place.

        Must be called AFTER features are built for the whole universe
        (the screener's first pass) because a rank needs all stocks on the
        same date. Columns ranked: return_5d, return_20d, volatility_20d,
        volume_ratio_5, ma_position_5, volume_ratio_20.
        """
        rank_cols = [
            "return_5d", "return_20d", "volatility_20d",
            "volume_ratio_5", "ma_position_5", "volume_ratio_20",
        ]
        by_date: Dict[str, list] = {}
        for code, feats in features_by_code.items():
            by_date.setdefault(str(feats.get("date", "")), []).append(feats)

        for _date, group in by_date.items():
            for col in rank_cols:
                vals = pd.Series(
                    [float(f.get(col, 0.0) or 0.0) for f in group], dtype=np.float64
                )
                ranks = vals.rank(pct=True)
                for feats, r in zip(group, ranks):
                    feats[f"rank_{col}"] = float(r)

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

        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT stock_code, trade_date::text
                    FROM market_data
                    WHERE stock_code = ANY(%s)
                      AND trade_date >= %s AND trade_date <= %s
                    ORDER BY stock_code, trade_date
                """, (stock_codes, start_date, end_date))
                available = cur.fetchall()
                cur.close()

                from collections import defaultdict
                dates_by_stock = defaultdict(list)
                for code, dt in available:
                    dates_by_stock[code].append(dt)
            except Exception as e:
                logger.warning(f"Failed to query available dates: {e}")
                dates_by_stock = {}
        else:
            dates_by_stock = {}

        # Pre-load market data for all stocks (batch)
        market_data_by_stock = {}
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                # Load up to 365 days of data per stock for lookback indicators (RSI, MA, etc.)
                lookback_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
                cur.execute("""
                    SELECT stock_code, trade_date, open_price, high_price, low_price, close_price, volume
                    FROM market_data
                    WHERE stock_code = ANY(%s)
                      AND trade_date >= %s AND trade_date <= %s
                    ORDER BY stock_code, trade_date
                """, (stock_codes, lookback_start, end_date))
                all_rows = cur.fetchall()
                cur.close()

                # Group by stock_code as DataFrames
                stock_data = defaultdict(list)
                for code, tdate, open_p, high_p, low_p, close_p, vol in all_rows:
                    stock_data[code].append({
                        'trade_date': tdate, 'open': open_p, 'high': high_p,
                        'low': low_p, 'close': close_p, 'volume': vol,
                    })

                for code, rows_list in stock_data.items():
                    df = pd.DataFrame(rows_list)
                    df = df.sort_values('trade_date').reset_index(drop=True)
                    market_data_by_stock[code] = df

                logger.info(f"Batch-loaded market data for {len(market_data_by_stock)} stocks ({len(all_rows)} rows)")
            except Exception as e:
                logger.warning(f"Batch market data load failed: {e}")
                market_data_by_stock = {}

        total_pairs = sum(len(d) for d in dates_by_stock.values())
        processed = 0
        for code in stock_codes:
            stock_dates = dates_by_stock.get(code, [])
            if not stock_dates:
                continue
            stock_market_df = market_data_by_stock.get(code, pd.DataFrame())
            for i, date_str in enumerate(stock_dates):
                try:
                    features = self.build_features(code, date_str, market_df=stock_market_df)
                    if features.get("feature_count", 0) >= 10:
                        rows.append(features)
                    processed += 1
                    if processed % 200 == 0:
                        logger.info(f"Build progress: {processed}/{total_pairs} stock-date pairs")
                except Exception as e:
                    logger.debug(f"Feature build failed for {code} {date_str}: {e}")
                    continue

        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def _build_advanced_features(
        self, stock_code: str, date: str,
        market_df: pd.DataFrame = None,
    ) -> Dict:
        """Build 19 advanced features: sector/market, volatility/risk, flow, ownership, credit/margin, technical."""

        features = {}

        close = None
        volume = None
        if market_df is not None and not market_df.empty:
            close_series = market_df.get("close_price", market_df.get("close"))
            if close_series is not None:
                close = close_series.values if hasattr(close_series, "values") else np.array(close_series)
                # Convert Decimal to float to avoid type errors in numpy operations
                close = np.array([float(c) for c in close])
            vol_series = market_df.get("volume")
            if vol_series is not None:
                volume = vol_series.values if hasattr(vol_series, "values") else np.array(vol_series)
                volume = np.array([float(v) for v in volume])

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
                self.pg_conn.rollback()

        # 2. relative_strength: stock_return / market_return
        features["relative_strength"] = 0.0
        if valid_close and len(close) >= 2:
            stock_ret = close[-1] / close[-2] - 1 if close[-2] != 0 else 0.0
            # TODO: fetch KOSPI index return from market_index table for accurate market_return
            market_return = 0.0
            features["relative_strength"] = float(stock_ret / market_return) if market_return != 0 else 0.0

        # 3. market_breadth: fraction of advancing stocks on the same date
        # (advancers / total). Requires the stock_prices table; fallback 0.0.
        features["market_breadth"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT COUNT(*) FILTER (WHERE close_price > prev_close),
                           COUNT(*)
                    FROM (
                        SELECT close_price,
                               LAG(close_price) OVER (
                                   PARTITION BY stock_code ORDER BY trade_date
                               ) AS prev_close
                        FROM stock_prices
                        WHERE trade_date = %s
                    ) t
                """, (date,))
                row = cur.fetchone()
                if row and row[1] and row[1] > 0:
                    features["market_breadth"] = float(row[0] / row[1])
                cur.close()
            except Exception:
                logger.debug("market_breadth unavailable; using 0.0")
                self.pg_conn.rollback()

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

        # 8. program_trading_ratio: program trading value / total value
        # Uses krx_program_trading table (foreign_buy_value + foreign_sell_value) / total_value
        features["program_trading_ratio"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT foreign_buy_value + foreign_sell_value, total_value
                    FROM krx_program_trading
                    WHERE trade_date = %s AND market = 'KOSPI'
                    ORDER BY trade_date DESC LIMIT 1
                """, (date,))
                row = cur.fetchone()
                if row and row[1] and row[1] > 0:
                    features["program_trading_ratio"] = float(row[0] / row[1])
                cur.close()
            except Exception:
                logger.debug("program_trading_ratio unavailable; using 0.0")
                self.pg_conn.rollback()

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
                self.pg_conn.rollback()

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
                self.pg_conn.rollback()

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
                self.pg_conn.rollback()

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
                self.pg_conn.rollback()

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
                self.pg_conn.rollback()

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
                self.pg_conn.rollback()

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
                self.pg_conn.rollback()

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
                self.pg_conn.rollback()

        # 19. short_selling_ratio: short selling volume / total volume
        # Uses krx_short_selling table (short_volume / total_volume)
        features["short_selling_ratio"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT short_volume, total_volume FROM krx_short_selling
                    WHERE stock_code = %s AND trade_date = %s
                """, (stock_code, date))
                row = cur.fetchone()
                if row and row[0] and row[1] and row[1] > 0:
                    features["short_selling_ratio"] = float(row[0] / row[1])
                cur.close()
            except Exception:
                logger.debug("short_selling_ratio unavailable; using 0.0")
                self.pg_conn.rollback()

        # ----- KRX Trading features -----

        # krx_total_trading_value: total KRX trading value for the date
        features["krx_total_trading_value"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT trading_value FROM krx_trading
                    WHERE trade_date = %s AND market = 'KOSPI' AND investor_type = 'Total'
                    LIMIT 1
                """, (date,))
                row = cur.fetchone()
                if row and row[0]:
                    features["krx_total_trading_value"] = float(row[0])
                cur.close()
            except Exception:
                logger.debug("krx_total_trading_value unavailable; using 0.0")
                self.pg_conn.rollback()

        # krx_advance_decline_ratio: net foreign buy / total trading value as proxy
        features["krx_advance_decline_ratio"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT net_buy, trading_value FROM krx_trading
                    WHERE trade_date = %s AND market = 'KOSPI' AND investor_type = 'Foreign'
                    LIMIT 1
                """, (date,))
                row = cur.fetchone()
                if row and row[1] and row[1] > 0:
                    features["krx_advance_decline_ratio"] = float(row[0] / row[1])
                cur.close()
            except Exception:
                logger.debug("krx_advance_decline_ratio unavailable; using 0.0")
                self.pg_conn.rollback()

        # ----- KRX Derivatives features -----

        # futures_premium: KOSPI200 futures close as proxy for market level
        features["futures_premium"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT close_price FROM krx_derivatives
                    WHERE trade_date = %s AND index_name = 'KOSPI200'
                    LIMIT 1
                """, (date,))
                row = cur.fetchone()
                if row and row[0]:
                    features["futures_premium"] = float(row[0])
                cur.close()
            except Exception:
                logger.debug("futures_premium unavailable; using 0.0")
                self.pg_conn.rollback()

        # derivatives_volume: total derivatives volume (KOSPI200 + KOSDAQ)
        features["derivatives_volume"] = 0.0
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute("""
                    SELECT SUM(volume) FROM krx_derivatives
                    WHERE trade_date = %s
                """, (date,))
                row = cur.fetchone()
                if row and row[0]:
                    features["derivatives_volume"] = float(row[0])
                cur.close()
            except Exception:
                logger.debug("derivatives_volume unavailable; using 0.0")
                self.pg_conn.rollback()

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

        # 23. beta_60d: 60-day beta to market
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

    def _get_stock_sentiment(self, stock_code: str, date: str) -> Dict:
        """Query stock_sentiment table for real sentiment data."""
        result = {
            "sentiment_avg_db": 0.0,
            "sentiment_momentum": 0.0,
        }
        if self.pg_conn is None:
            return result
        try:
            cur = self.pg_conn.cursor()
            cur.execute("""
                SELECT avg_sentiment, positive_count, negative_count, sentiment_count
                FROM stock_sentiment
                WHERE stock_code = %s AND analysis_date = %s
                LIMIT 1
            """, (stock_code, date))
            row = cur.fetchone()
            if row and row[0] is not None:
                result["sentiment_avg_db"] = float(row[0])
                # sentiment_momentum: positive ratio minus negative ratio
                total = (row[1] or 0) + (row[2] or 0)
                if total > 0:
                    result["sentiment_momentum"] = float((row[1] - row[2]) / total)
            cur.close()
        except Exception:
            logger.debug("_get_stock_sentiment unavailable; using 0.0")
            self.pg_conn.rollback()
        return result

    def _get_economic_events(self, date: str) -> Dict:
        """Query economic_events table for event count and impact in past 7 days."""
        result = {
            "economic_event_count_7d": 0.0,
            "economic_event_impact": 0.0,
        }
        if self.pg_conn is None:
            return result
        try:
            cur = self.pg_conn.cursor()
            past_7d = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
            cur.execute("""
                SELECT COUNT(*), SUM(CASE WHEN importance = 'high' THEN 3 WHEN importance = 'medium' THEN 1 ELSE 0 END)
                FROM economic_events
                WHERE event_date >= %s AND event_date <= %s
            """, (past_7d, date))
            row = cur.fetchone()
            if row:
                result["economic_event_count_7d"] = float(row[0] or 0)
                result["economic_event_impact"] = float(row[1] or 0)
            cur.close()
        except Exception:
            logger.debug("_get_economic_events unavailable; using 0.0")
            self.pg_conn.rollback()
        return result

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
            "value_per", "value_pbr", "value_psr", "value_pcr", "value_ncav",
            "value_ev_ebit", "value_pfcr",
            "quality_cp_to_assets", "quality_op_to_equity", "quality_roe",
            "quality_roa", "quality_f_score", "quality_asset_growth",
            "quality_debt_ratio_change", "quality_op_growth",
            "quality_earnings_volatility", "quality_price_volatility_60d",
            "quality_beta",
            "momentum_1m_reverse", "momentum_3_12m", "momentum_op", "momentum_ni",

            "sector_momentum", "relative_strength",
            "vix_proxy", "volatility_skew",
            "program_trading_ratio", "etf_flow_5d",
            "foreign_ownership_pct", "institution_ownership_pct", "retail_ownership_pct",
            "short_interest_ratio", "days_to_cover",
            "margin_balance_change", "credit_balance_change", "short_selling_ratio",
            "volatility_20d_rank", "volume_ratio_vs_avg", "price_vs_sector",
            "beta_60d", "momentum_divergence",

            # KRX Trading features
            "krx_total_trading_value", "krx_advance_decline_ratio",

            # KRX Derivatives features
            "futures_premium", "derivatives_volume",

            # Stock sentiment features (from stock_sentiment table)
            "sentiment_avg_db", "sentiment_momentum",

            # Economic event features
            "economic_event_count_7d", "economic_event_impact",

            # Interaction features (computed in Trainer.prepare_training_data)
            "momentum_vs_volatility", "trend_interaction", "volume_price_trend",
            "cross_trend", "volatility_volume", "short_medium_term_momentum",
            "trend_confirmation", "price_volume",

            # Rolling statistics (computed in Trainer.prepare_training_data)
            "return_5d_mean_10d", "volatility_20d_mean_10d", "volume_ratio_5_mean_10d",

            # Cross-sectional percentile rank features (computed in Trainer.prepare_training_data)
            "rank_return_5d", "rank_return_20d", "rank_volatility_20d",
            "rank_volume_ratio_5", "rank_ma_position_5", "rank_volume_ratio_20",

            # Rolling target encoding features (computed in Trainer.prepare_training_data)
            "target_ma_5", "target_ma_10", "target_ma_20",

            # Kalman filter features (denoised momentum)
            "kalman_momentum_1d", "kalman_momentum_5d", "kalman_volatility",

            # Bayesian momentum/factor features (parallel path, A/B against Kalman)
            "bayes_momentum_1d", "bayes_momentum_5d",
            "bayes_volatility", "bayes_gain_uncertainty",

            # Quality score (F-Score, 0~1)
            "quality_score",

            # News event features (market impact, event taxonomy, theme exposure)
            "market_impact_score",
            "event_realized_5d", "event_mna_5d", "event_capital_increase_5d",
            "event_cb_bw_5d", "event_stake_change_5d", "event_contract_5d",
            "event_new_product_5d", "event_patent_5d", "event_regulation_5d",
            "event_litigation_5d", "event_delisting_5d", "event_recall_5d",
            "event_treasury_5d", "event_exec_change_5d", "event_partnership_5d",
            "event_macro_5d", "event_market_liquidity_5d", "event_disaster_5d",
            "theme_exposure_5d",
        ])

    def set_db_connections(self, pg_conn=None, neo4j_conn=None):
        """Set or update database connections."""
        self.pg_conn = pg_conn
        self.neo4j_conn = neo4j_conn

    def clear_cache(self):
        """Clear the feature cache."""
        self._cache.clear()
