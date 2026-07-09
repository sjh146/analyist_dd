"""
Market Features
Extracts 22 features from price, volume, foreign/institutional, and derivatives data.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class MarketFeatures:
    """Features derived from OHLCV, foreign/institutional supply, and derivatives data."""

    def get_price_features(self, df: pd.DataFrame) -> Dict:
        """Calculate price-based features (8 features)."""
        if df.empty:
            return self._empty_price_features()

        close = df.get("close_price", df.get("close"))
        if close is None or len(close) == 0:
            return self._empty_price_features()

        close = close.values if hasattr(close, "values") else np.array(close)
        valid_len = len(close)

        features = {}
        features["price"] = float(close[-1]) if valid_len >= 1 else 0.0
        features["return_1d"] = float(close[-1] / close[-2] - 1) if valid_len >= 2 else 0.0
        features["return_5d"] = float(close[-1] / close[-5] - 1) if valid_len >= 5 else 0.0
        features["return_20d"] = float(close[-1] / close[-20] - 1) if valid_len >= 20 else 0.0

        if valid_len >= 21:
            rets = [close[i] / close[i - 1] - 1 for i in range(1, valid_len)]
            features["volatility_20d"] = float(np.std(rets[-20:]))
            features["volatility_60d"] = float(np.std(rets[-60:])) if len(rets) >= 60 else float(np.std(rets))
        else:
            features["volatility_20d"] = 0.0
            features["volatility_60d"] = 0.0

        features["ma_position_5"] = float(close[-1] / np.mean(close[-5:]) - 1) * 100 if valid_len >= 5 else 0.0
        features["ma_position_20"] = float(close[-1] / np.mean(close[-20:]) - 1) * 100 if valid_len >= 20 else 0.0
        features["ma_position_60"] = float(close[-1] / np.mean(close[-60:]) - 1) * 100 if valid_len >= 60 else 0.0
        features["ma_position_120"] = float(close[-1] / np.mean(close[-120:]) - 1) * 100 if valid_len >= 120 else 0.0

        return features

    def get_technical_features(self, df: pd.DataFrame) -> Dict:
        """Extract technical indicator features from pre-calculated columns (8 features)."""
        features = {}

        col_map = {
            "rsi": 50.0,
            "macd": 0.0,
            "macd_signal": 0.0,
            "macd_hist": 0.0,
            "bb_width": 0.0,
            "atr": 0.0,
            "stoch_k": 50.0,
            "stoch_d": 50.0,
            "obv": 0.0,
        }

        for col, default in col_map.items():
            if col in df.columns:
                val = df[col].values[-1] if len(df) > 0 else default
                features[col] = float(val) if not pd.isna(val) else default

        if "bb_middle" in df.columns and "close" in df.columns:
            bb_mid = df["bb_middle"].values[-1]
            close_val = df["close"].values[-1] if "close" in df.columns else df.get("close_price", pd.Series([0])).values[-1]
            features["bb_position"] = float((close_val - bb_mid) / bb_mid * 100) if bb_mid else 0.0

        if "macd" in features and "atr" in features:
            atr = features.get("atr", 0.0)
            features["atr_pct"] = float(features["price"] and atr / features["price"] * 100) if features.get("price", 0) else 0.0

        if not features.get("bb_position", None):
            features["bb_position"] = 0.0
        if not features.get("atr_pct", None):
            features["atr_pct"] = 0.0

        return features

    def get_volume_features(self, df: pd.DataFrame) -> Dict:
        """Calculate volume-based features (2 features)."""
        features = {}
        if "volume" not in df.columns or df.empty:
            features["volume_ratio_5"] = 0.0
            features["volume_ratio_20"] = 0.0
            return features

        vol = df["volume"].values
        if len(vol) >= 5:
            avg_5 = np.mean(vol[-5:])
            features["volume_ratio_5"] = float(vol[-1] / avg_5) if avg_5 else 0.0
        else:
            features["volume_ratio_5"] = 0.0

        if len(vol) >= 20:
            avg_20 = np.mean(vol[-20:])
            features["volume_ratio_20"] = float(vol[-1] / avg_20) if avg_20 else 0.0
        else:
            features["volume_ratio_20"] = 0.0

        return features

    def get_supply_features(self, stock_code: str, db_conn=None) -> Dict:
        """Get foreign/institutional supply features from DB (4 features)."""
        features = {
            "foreign_net_buy": 0.0,
            "foreign_net_buy_5d": 0.0,
            "institution_net_buy": 0.0,
            "institution_net_buy_5d": 0.0,
        }

        if db_conn is None:
            return features

        try:
            query = """
                SELECT trade_date, foreign_net_buy, institution_net_buy
                FROM foreign_institutional
                WHERE stock_code = %s
                ORDER BY trade_date DESC
                LIMIT 6
            """
            cur = db_conn.cursor()
            cur.execute(query, (stock_code,))
            rows = cur.fetchall()
            cur.close()

            if rows:
                features["foreign_net_buy"] = float(rows[0][1]) if rows[0][1] else 0.0
                features["institution_net_buy"] = float(rows[0][2]) if rows[0][2] else 0.0

                foreign_vals = [float(r[1]) for r in rows[:5] if r[1] is not None]
                if foreign_vals:
                    features["foreign_net_buy_5d"] = float(np.mean(foreign_vals))

                inst_vals = [float(r[2]) for r in rows[:5] if r[2] is not None]
                if inst_vals:
                    features["institution_net_buy_5d"] = float(np.mean(inst_vals))

        except Exception as e:
            logger.debug(f"Supply features failed for {stock_code}: {e}")

        return features

    def get_derivatives_features(self, db_conn=None) -> Dict:
        """Get derivatives features from DB (2 features)."""
        features = {"basis": 0.0, "basis_change_5d": 0.0}

        if db_conn is None:
            return features

        try:
            query = """
                SELECT trade_date, basis
                FROM futures_options
                ORDER BY trade_date DESC
                LIMIT 6
            """
            cur = db_conn.cursor()
            cur.execute(query)
            rows = cur.fetchall()
            cur.close()

            if rows:
                features["basis"] = float(rows[0][1]) if rows[0][1] else 0.0
                if len(rows) >= 6:
                    latest = float(rows[0][1]) if rows[0][1] else 0.0
                    prev = float(rows[5][1]) if rows[5][1] else 0.0
                    features["basis_change_5d"] = latest - prev

        except Exception as e:
            logger.debug(f"Derivatives features failed: {e}")

        return features

    def get_all_features(
        self, df: pd.DataFrame, stock_code: str, db_conn=None,
    ) -> Dict:
        """Build all 22+ market features from price data, DB-connected supply and derivatives."""
        features = {}
        features.update(self.get_price_features(df))
        features.update(self.get_technical_features(df))
        features.update(self.get_volume_features(df))
        features.update(self.get_supply_features(stock_code, db_conn))
        features.update(self.get_derivatives_features(db_conn))
        return features

    def _empty_price_features(self) -> Dict:
        return {
            "price": 0.0, "return_1d": 0.0, "return_5d": 0.0, "return_20d": 0.0,
            "volatility_20d": 0.0, "volatility_60d": 0.0,
            "ma_position_5": 0.0, "ma_position_20": 0.0,
            "ma_position_60": 0.0, "ma_position_120": 0.0,
        }
