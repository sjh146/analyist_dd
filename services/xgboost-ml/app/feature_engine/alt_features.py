"""
Alternative Signal Features
Sentiment surge, cross-asset correlation, flow strength, and short squeeze detection.
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class AlternativeFeatures:
    """Features from sentiment surges, cross-asset correlations, flow analysis, and short squeeze."""

    def sentiment_surge(
        self, stock_code: str, date: str, lookback: int = 3, threshold: float = 2.0, db_conn=None
    ) -> Dict:
        """Detect sentiment surge over lookback period.
        Returns 1.0 (positive surge), -1.0 (negative surge), or 0.0.
        """
        if db_conn is None:
            return {"sentiment_surge": 0.0}

        try:
            cur = db_conn.cursor()
            cur.execute(
                """
                SELECT analysis_date, avg_sentiment
                FROM stock_sentiment
                WHERE stock_code = %s AND analysis_date <= %s
                ORDER BY analysis_date DESC
                LIMIT %s
                """,
                (stock_code, date, lookback),
            )
            rows = cur.fetchall()
            cur.close()

            if len(rows) >= 2:
                current = float(rows[0][1]) if rows[0][1] is not None else 0.0
                prev = float(rows[-1][1]) if rows[-1][1] is not None else 0.0
                ratio = (current - prev) / (abs(prev) + 1e-8)

                if ratio > threshold:
                    return {"sentiment_surge": 1.0}
                if ratio < -threshold:
                    return {"sentiment_surge": -1.0}

            return {"sentiment_surge": 0.0}
        except Exception as e:
            logger.debug("sentiment_surge failed for %s: %s", stock_code, e)
            if db_conn:
                db_conn.rollback()
            return {"sentiment_surge": 0.0}

    def cross_asset_correlation(
        self, stock_code: str, date: str, window: int = 20, db_conn=None
    ) -> Dict:
        """Rolling Pearson correlation of stock returns with FX, oil, and interest rate changes."""
        features = {"fx_corr_20d": 0.0, "oil_corr_20d": 0.0, "rate_corr_20d": 0.0}
        if db_conn is None:
            return features

        try:
            cur = db_conn.cursor()

            cur.execute(
                """
                SELECT trade_date, close_price
                FROM market_data
                WHERE stock_code = %s AND trade_date <= %s
                ORDER BY trade_date DESC
                LIMIT %s
                """,
                (stock_code, date, window + 1),
            )
            price_rows = cur.fetchall()
            if len(price_rows) < window + 1:
                cur.close()
                return features

            stock_s = pd.Series(
                {r[0]: float(r[1]) for r in price_rows if r[1] is not None}
            )
            stock_rets = stock_s.sort_index().pct_change().dropna()
            if len(stock_rets) < 2:
                cur.close()
                return features

            macro_specs = [
                ("USD/KRW 환율", "fx_corr_20d", "pct"),
                ("WTI 유가", "oil_corr_20d", "pct"),
                ("기준금리", "rate_corr_20d", "diff"),
            ]

            for name, feat_key, change_type in macro_specs:
                cur.execute(
                    """
                    SELECT date, value
                    FROM macro_indicators
                    WHERE indicator_name = %s AND date <= %s
                    ORDER BY date DESC
                    LIMIT %s
                    """,
                    (name, date, window + 1),
                )
                macro_rows = cur.fetchall()
                if len(macro_rows) < 2:
                    continue

                macro_s = pd.Series(
                    {r[0]: float(r[1]) for r in macro_rows if r[1] is not None}
                ).sort_index()

                if change_type == "diff":
                    macro_chg = macro_s.diff().dropna()
                else:
                    macro_chg = macro_s.pct_change().dropna()

                common = stock_rets.index.intersection(macro_chg.index)
                if len(common) < 2:
                    continue

                s = stock_rets[common].values
                m = macro_chg[common].values

                if np.std(s) == 0 or np.std(m) == 0:
                    continue

                corr = np.corrcoef(s, m)[0, 1]
                if not np.isnan(corr):
                    features[feat_key] = float(corr)

            cur.close()
        except Exception as e:
            logger.debug("cross_asset_correlation failed for %s: %s", stock_code, e)
            if db_conn:
                db_conn.rollback()

        return features

    def flow_strength(
        self, stock_code: str, date: str, window: int = 5, db_conn=None
    ) -> Dict:
        """Z-score of cumulative foreign/institutional net buy over window vs 20-day rolling sums."""
        features = {"foreign_flow_z": 0.0, "institution_flow_z": 0.0}
        if db_conn is None:
            return features

        try:
            cur = db_conn.cursor()
            cur.execute(
                """
                SELECT trade_date, foreign_net_buy, institution_net_buy
                FROM foreign_institutional
                WHERE stock_code = %s AND trade_date <= %s
                ORDER BY trade_date DESC
                LIMIT %s
                """,
                (stock_code, date, window + 20),
            )
            rows = cur.fetchall()
            cur.close()

            if len(rows) < window + 1:
                return features

            rows.reverse()
            foreign_vals = np.array(
                [float(r[1]) if r[1] is not None else 0.0 for r in rows]
            )
            inst_vals = np.array(
                [float(r[2]) if r[2] is not None else 0.0 for r in rows]
            )

            if len(foreign_vals) < window:
                return features

            foreign_roll = np.array(
                [foreign_vals[i : i + window].sum() for i in range(len(foreign_vals) - window + 1)]
            )
            inst_roll = np.array(
                [inst_vals[i : i + window].sum() for i in range(len(inst_vals) - window + 1)]
            )

            if len(foreign_roll) < 2:
                return features

            f_mean, f_std = np.mean(foreign_roll), np.std(foreign_roll)
            i_mean, i_std = np.mean(inst_roll), np.std(inst_roll)

            if f_std > 0:
                features["foreign_flow_z"] = float((foreign_roll[-1] - f_mean) / f_std)
            if i_std > 0:
                features["institution_flow_z"] = float((inst_roll[-1] - i_mean) / i_std)

        except Exception as e:
            logger.debug("flow_strength failed for %s: %s", stock_code, e)
            if db_conn:
                db_conn.rollback()

        return features

    def short_squeeze(self, stock_code: str, date: str, db_conn=None) -> Dict:
        """Detect short squeeze: short_ratio drop >20% AND 5-day return >5%."""
        if db_conn is None:
            return {"short_squeeze": 0.0}

        try:
            cur = db_conn.cursor()

            cur.execute(
                """
                SELECT trade_date, short_ratio
                FROM krx_short_selling
                WHERE stock_code = %s AND trade_date <= %s
                ORDER BY trade_date DESC
                LIMIT 2
                """,
                (stock_code, date),
            )
            short_rows = cur.fetchall()
            if len(short_rows) < 2:
                cur.close()
                return {"short_squeeze": 0.0}

            current_ratio = float(short_rows[0][1]) if short_rows[0][1] is not None else 0.0
            prev_ratio = float(short_rows[1][1]) if short_rows[1][1] is not None else 0.0

            if prev_ratio <= 0:
                cur.close()
                return {"short_squeeze": 0.0}

            ratio_drop = (prev_ratio - current_ratio) / prev_ratio

            cur.execute(
                """
                SELECT trade_date, close_price
                FROM market_data
                WHERE stock_code = %s AND trade_date <= %s
                ORDER BY trade_date DESC
                LIMIT 5
                """,
                (stock_code, date),
            )
            price_rows = cur.fetchall()
            cur.close()

            if len(price_rows) < 5:
                return {"short_squeeze": 0.0}

            current_price = float(price_rows[0][1]) if price_rows[0][1] is not None else 0.0
            price_5d_ago = float(price_rows[-1][1]) if price_rows[-1][1] is not None else 0.0

            if price_5d_ago <= 0:
                return {"short_squeeze": 0.0}

            return_5d = (current_price - price_5d_ago) / price_5d_ago

            if ratio_drop > 0.20 and return_5d > 0.05:
                return {"short_squeeze": 1.0}

            return {"short_squeeze": 0.0}

        except Exception as e:
            logger.debug("short_squeeze failed for %s: %s", stock_code, e)
            if db_conn:
                db_conn.rollback()
            return {"short_squeeze": 0.0}

    def compute_all(self, stock_code: str, date: str, db_conn=None) -> Dict:
        """Compute all 7 alternative signal features."""
        features = {}
        features.update(self.sentiment_surge(stock_code, date, db_conn=db_conn))
        features.update(self.cross_asset_correlation(stock_code, date, db_conn=db_conn))
        features.update(self.flow_strength(stock_code, date, db_conn=db_conn))
        features.update(self.short_squeeze(stock_code, date, db_conn=db_conn))
        return features
