"""TA-Lib Technical Analysis Features"""

import numpy as np
import pandas as pd
import talib
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class TAFeatures:
    """Compute TA-Lib based technical indicators for ML feature engineering."""

    def compute_all(
        self, stock_code: str, date: str, df: Optional[pd.DataFrame] = None, pg_conn=None
    ) -> Dict:
        """Compute 30+ TA-Lib features for a single stock on a given date."""
        if df is None or df.empty:
            if pg_conn is not None:
                try:
                    cur = pg_conn.cursor()
                    cur.execute("""
                        SELECT trade_date, open_price, high_price, low_price, close_price, volume
                        FROM market_data
                        WHERE stock_code = %s AND trade_date <= %s
                        ORDER BY trade_date
                    """, (stock_code, date))
                    rows = cur.fetchall()
                    cur.close()
                    if rows:
                        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
                except Exception as e:
                    logger.debug(f"TAFeatures: failed to load market data for {stock_code}: {e}")
                    if pg_conn:
                        pg_conn.rollback()
                    return self._empty_features()

        if df is None or df.empty:
            return self._empty_features()

        close = df["close"].values.astype(np.float64)
        high = df["high"].values.astype(np.float64)
        low = df["low"].values.astype(np.float64)
        volume = df["volume"].values.astype(np.float64) if "volume" in df.columns else None
        n = len(close)

        features = {}

        # --- Trend Indicators ---
        features.update(self._trend_features(close, n))

        # --- Momentum Indicators ---
        features.update(self._momentum_features(close, high, low, n))

        # --- Volatility Indicators ---
        features.update(self._volatility_features(close, high, low, n))

        # --- Volume Indicators ---
        features.update(self._volume_features(close, high, low, volume, n))

        features["feature_count"] = len(features)
        features["stock_code"] = stock_code
        features["date"] = date
        return features

    def _safe(self, value, default=0.0):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return default
        return float(value)

    def _trend_features(self, close: np.ndarray, n: int) -> Dict:
        f = {}
        if n >= 5:
            f["SMA_5"] = self._safe(talib.SMA(close, 5)[-1])
        if n >= 20:
            f["SMA_20"] = self._safe(talib.SMA(close, 20)[-1])
        if n >= 60:
            f["SMA_60"] = self._safe(talib.SMA(close, 60)[-1])
        if n >= 5:
            f["EMA_5"] = self._safe(talib.EMA(close, 5)[-1])
        if n >= 20:
            f["EMA_20"] = self._safe(talib.EMA(close, 20)[-1])
        if n >= 60:
            f["EMA_60"] = self._safe(talib.EMA(close, 60)[-1])
        if n >= 26:
            macd, macd_signal, macd_hist = talib.MACD(close)
            f["MACD"] = self._safe(macd[-1])
            f["MACD_signal"] = self._safe(macd_signal[-1])
        if n >= 14:
            f["ADX"] = self._safe(talib.ADX(close, close, close, 14)[-1])
            f["ADXR"] = self._safe(talib.ADXR(close, close, close, 14)[-1])
        return f

    def _momentum_features(self, close: np.ndarray, high: np.ndarray, low: np.ndarray, n: int) -> Dict:
        f = {}
        if n >= 14:
            f["RSI_14"] = self._safe(talib.RSI(close, 14)[-1])
        if n >= 14:
            slowk, slowd = talib.STOCH(high, low, close)
            f["Stoch_K"] = self._safe(slowk[-1])
            f["Stoch_D"] = self._safe(slowd[-1])
        if n >= 14:
            f["Williams_%R"] = self._safe(talib.WILLR(high, low, close, 14)[-1])
        if n >= 20:
            f["CCI_20"] = self._safe(talib.CCI(high, low, close, 20)[-1])
        if n >= 5:
            f["ROC_5"] = self._safe(talib.ROC(close, 5)[-1])
        if n >= 20:
            f["ROC_20"] = self._safe(talib.ROC(close, 20)[-1])
        if n >= 26:
            momentum = np.diff(close, prepend=close[0])
            abs_momentum = np.abs(momentum)
            ema1 = talib.EMA(momentum, 25)
            ema2 = talib.EMA(ema1, 13)
            ema_abs1 = talib.EMA(abs_momentum, 25)
            ema_abs2 = talib.EMA(ema_abs1, 13)
            tsi = 100.0 * ema2 / ema_abs2
            f["TSI"] = self._safe(tsi[-1])
        return f

    def _volatility_features(self, close: np.ndarray, high: np.ndarray, low: np.ndarray, n: int) -> Dict:
        f = {}
        if n >= 20:
            upper, middle, lower = talib.BBANDS(close, 20, 2, 2)
            f["BB_upper"] = self._safe(upper[-1])
            f["BB_lower"] = self._safe(lower[-1])
            f["BB_middle"] = self._safe(middle[-1])
            bb_width = (upper[-1] - lower[-1]) / middle[-1] if middle[-1] != 0 else 0.0
            f["BB_width"] = self._safe(bb_width)
            bb_pos = (close[-1] - middle[-1]) / (upper[-1] - lower[-1]) if (upper[-1] - lower[-1]) != 0 else 0.0
            f["BB_position"] = self._safe(bb_pos)
        if n >= 14:
            atr = talib.ATR(high, low, close, 14)[-1]
            f["ATR"] = self._safe(atr)
            f["ATR_pct"] = self._safe(atr / close[-1] * 100.0) if close[-1] != 0 else 0.0
        return f

    def _volume_features(
        self, close: np.ndarray, high: np.ndarray, low: np.ndarray, volume: Optional[np.ndarray], n: int
    ) -> Dict:
        f = {}
        if volume is not None and n > 1:
            f["OBV"] = self._safe(talib.OBV(close, volume)[-1])
        if volume is not None and n >= 14:
            f["MFI_14"] = self._safe(talib.MFI(high, low, close, volume, 14)[-1])
        if volume is not None:
            if n >= 5:
                f["Volume_ma_ratio_5"] = self._safe(volume[-1] / np.mean(volume[-5:])) if np.mean(volume[-5:]) != 0 else 0.0
            if n >= 20:
                f["Volume_ma_ratio_20"] = self._safe(volume[-1] / np.mean(volume[-20:])) if np.mean(volume[-20:]) != 0 else 0.0
            if n >= 60:
                f["Volume_ma_ratio_60"] = self._safe(volume[-1] / np.mean(volume[-60:])) if np.mean(volume[-60:]) != 0 else 0.0
        return f

    def _empty_features(self) -> Dict:
        return {
            "SMA_5": 0.0, "SMA_20": 0.0, "SMA_60": 0.0,
            "EMA_5": 0.0, "EMA_20": 0.0, "EMA_60": 0.0,
            "MACD": 0.0, "MACD_signal": 0.0,
            "ADX": 0.0, "ADXR": 0.0,
            "RSI_14": 0.0, "Stoch_K": 0.0, "Stoch_D": 0.0,
            "Williams_%R": 0.0, "CCI_20": 0.0, "ROC_5": 0.0, "ROC_20": 0.0, "TSI": 0.0,
            "BB_upper": 0.0, "BB_lower": 0.0, "BB_middle": 0.0,
            "BB_width": 0.0, "BB_position": 0.0,
            "ATR": 0.0, "ATR_pct": 0.0,
            "OBV": 0.0, "MFI_14": 0.0,
            "Volume_ma_ratio_5": 0.0, "Volume_ma_ratio_20": 0.0, "Volume_ma_ratio_60": 0.0,
            "feature_count": 0, "stock_code": "", "date": "",
        }
