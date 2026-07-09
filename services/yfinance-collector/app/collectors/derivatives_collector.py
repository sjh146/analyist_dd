"""
Derivatives Collector
Collects KOSPI200 futures and options data from yfinance.
"""

import yfinance as yf
import pandas as pd
import logging
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class DerivativesCollector:
    """Collects KOSPI200 futures/options data."""

    KOSPI200_FUTURES = "KOSPI200.KS"
    KOSPI200_INDEX = "^KS200"

    def __init__(self):
        self.end_date = datetime.now()
        self.start_date = self.end_date - timedelta(days=365)

    def collect_derivatives_data(self) -> pd.DataFrame:
        """
        Collect KOSPI200 futures and calculate basis.
        Attempts multiple ticker formats.

        Returns:
            DataFrame with: trade_date, futures_price, options_volume,
            basis, options_put_call_ratio
        """
        rows = []

        for ticker_symbol in [self.KOSPI200_FUTURES, self.KOSPI200_INDEX]:
            try:
                ticker = yf.Ticker(ticker_symbol)
                df = ticker.history(start=self.start_date, end=self.end_date)

                if df.empty:
                    continue

                df = df.reset_index()
                for _, row in df.iterrows():
                    rows.append({
                        "trade_date": row["Date"].date()
                            if isinstance(row["Date"], pd.Timestamp) else row["Date"],
                        "futures_price": float(row["Close"]),
                        "options_volume": 0,
                        "basis": 0.0,
                        "options_put_call_ratio": 0.0,
                    })

                logger.info(f"Collected {len(rows)} days of derivatives data from {ticker_symbol}")
                break

            except Exception as e:
                logger.debug(f"Failed to collect from {ticker_symbol}: {e}")
                continue

        return pd.DataFrame(rows)

    def calculate_basis(self, futures_df: pd.DataFrame, spot_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate futures-spot basis.
        basis = (futures_price - spot_price) / spot_price * 100
        """
        if futures_df.empty or spot_df.empty:
            return futures_df

        spot_price_map = spot_df.set_index("trade_date")["close"].to_dict()
        basis_values = []

        for _, row in futures_df.iterrows():
            spot = spot_price_map.get(row["trade_date"], None)
            if spot and spot > 0:
                basis_values.append((row["futures_price"] - spot) / spot * 100)
            else:
                basis_values.append(0.0)

        futures_df["basis"] = basis_values
        return futures_df
