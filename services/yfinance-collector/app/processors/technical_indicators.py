"""
Technical Indicator Calculator
Calculates common technical indicators from price data.
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class TechnicalIndicatorCalculator:
    """Calculates technical indicators for stock data."""

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all technical indicators."""
        if df.empty:
            return df
        try:
            df = self.calculate_moving_averages(df)
            df = self.calculate_ema(df)
            df = self.calculate_rsi(df)
            df = self.calculate_macd(df)
            df = self.calculate_bollinger_bands(df)
            df = self.calculate_atr(df)
            df = self.calculate_stochastic(df)
            df = self.calculate_obv(df)
            df = self.calculate_volume_indicators(df)
            return df
        except Exception as e:
            logger.error(f"Failed to calculate indicators: {e}")
            return df

    def calculate_moving_averages(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate SMAs: 5, 20, 60, 120."""
        for w in [5, 20, 60, 120]:
            df[f"sma_{w}"] = df.groupby("stock_code")["close"].transform(
                lambda x: x.rolling(window=w, min_periods=1).mean().shift(1)
            )
        return df

    def calculate_ema(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMAs: 12, 26."""
        for w in [12, 26]:
            df[f"ema_{w}"] = df.groupby("stock_code")["close"].transform(
                lambda x: x.ewm(span=w, adjust=False).mean().shift(1)
            )
        return df

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Calculate RSI(14)."""

        def _rsi(series):
            delta = series.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta.where(delta < 0, 0.0))
            avg_gain = gain.rolling(window=period, min_periods=1).mean()
            avg_loss = loss.rolling(window=period, min_periods=1).mean()
            rs = avg_gain / avg_loss.replace(0, np.nan)
            rsi_val = 100 - (100 / (1 + rs))
            return rsi_val.shift(1)

        df["rsi"] = df.groupby("stock_code")["close"].transform(_rsi)
        return df

    def calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate MACD(12,26,9) with signal and histogram."""

        def _macd(series):
            exp1 = series.ewm(span=12, adjust=False).mean()
            exp2 = series.ewm(span=26, adjust=False).mean()
            macd_line = (exp1 - exp2).shift(1)
            signal = macd_line.ewm(span=9, adjust=False).mean()
            return pd.DataFrame({
                "macd": macd_line,
                "macd_signal": signal,
                "macd_hist": macd_line - signal,
            })

        result = df.groupby("stock_code")["close"].apply(_macd)
        for col in ["macd", "macd_signal", "macd_hist"]:
            df[col] = result.xs(col, level=-1)
        return df

    def calculate_bollinger_bands(
        self, df: pd.DataFrame, period: int = 20, std_dev: int = 2
    ) -> pd.DataFrame:
        """Calculate Bollinger Bands(20, 2)."""

        def _bb(series):
            ma = series.rolling(window=period, min_periods=1).mean().shift(1)
            std = series.rolling(window=period, min_periods=1).std().shift(1)
            return pd.DataFrame({
                "bb_middle": ma,
                "bb_upper": ma + (std * std_dev),
                "bb_lower": ma - (std * std_dev),
                "bb_width": ((std * std_dev * 2) / ma.replace(0, np.nan)) * 100,
            })

        result = df.groupby("stock_code")["close"].apply(_bb)
        for col in ["bb_middle", "bb_upper", "bb_lower", "bb_width"]:
            df[col] = result.xs(col, level=-1)
        return df

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Calculate ATR(14)."""

        def _atr(group):
            high = group["high"]
            low = group["low"]
            close_prev = group["close"].shift(1)
            tr1 = high - low
            tr2 = (high - close_prev).abs()
            tr3 = (low - close_prev).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_val = tr.rolling(window=period, min_periods=1).mean().shift(1)
            return atr_val

        df["atr"] = df.groupby("stock_code")[["high", "low", "close"]].apply(_atr).reset_index(level=0, drop=True)
        return df

    def calculate_stochastic(
        self, df: pd.DataFrame, k_period: int = 14, d_period: int = 3
    ) -> pd.DataFrame:
        """Calculate Stochastic Oscillator (14, 3, 3)."""

        def _stoch(group):
            low_min = group["low"].rolling(window=k_period, min_periods=1).min()
            high_max = group["high"].rolling(window=k_period, min_periods=1).max()
            fast_k = ((group["close"] - low_min) / (high_max - low_min).replace(0, np.nan)) * 100
            fast_k = fast_k.shift(1)
            slow_k = fast_k.rolling(window=d_period, min_periods=1).mean()
            slow_d = slow_k.rolling(window=d_period, min_periods=1).mean()
            return pd.DataFrame({"stoch_k": slow_k, "stoch_d": slow_d})

        result = df.groupby("stock_code")[["high", "low", "close"]].apply(_stoch)
        for col in ["stoch_k", "stoch_d"]:
            df[col] = result.xs(col, level=-1)
        return df

    def calculate_obv(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate On-Balance Volume."""

        def _obv(group):
            close_diff = group["close"].diff()
            direction = (close_diff > 0).astype(int) - (close_diff < 0).astype(int)
            obv_val = (direction * group["volume"]).cumsum()
            return obv_val.shift(1)

        df["obv"] = df.groupby("stock_code")[["close", "volume"]].apply(
            lambda g: _obv(g)
        ).reset_index(level=0, drop=True)
        return df

    def calculate_volume_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate volume-based indicators."""
        df["volume_ma_5"] = df.groupby("stock_code")["volume"].transform(
            lambda x: x.rolling(window=5, min_periods=1).mean().shift(1)
        )
        df["volume_ma_20"] = df.groupby("stock_code")["volume"].transform(
            lambda x: x.rolling(window=20, min_periods=1).mean().shift(1)
        )
        df["volume_ratio_5"] = df["volume"] / df["volume_ma_5"].replace(0, np.nan)
        df["volume_ratio_20"] = df["volume"] / df["volume_ma_20"].replace(0, np.nan)
        return df

    def calculate_foreign_ma(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate foreign net buy moving averages (5d, 20d)."""
        if "foreign_net_buy" not in df.columns:
            return df
        df["foreign_net_buy_ma_5"] = df.groupby("stock_code")["foreign_net_buy"].transform(
            lambda x: x.rolling(window=5, min_periods=1).mean().shift(1)
        )
        df["foreign_net_buy_ma_20"] = df.groupby("stock_code")["foreign_net_buy"].transform(
            lambda x: x.rolling(window=20, min_periods=1).mean().shift(1)
        )
        return df

    def calculate_basis_change(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate basis (futures - spot) change rates."""
        if "basis" not in df.columns:
            return df
        df["basis_change_5d"] = df.groupby("stock_code")["basis"].transform(
            lambda x: x.pct_change(periods=5).shift(1)
        )
        return df

    def calculate_macro_changes(self, df: pd.DataFrame, col: str) -> pd.DataFrame:
        """Calculate month-over-month and year-over-year changes for a macro column."""
        if col not in df.columns:
            return df
        df[f"{col}_mom"] = df[col].pct_change(periods=1)
        df[f"{col}_yoy"] = df[col].pct_change(periods=12)
        return df
