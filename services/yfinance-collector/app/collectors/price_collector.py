"""
Price Collector
Downloads OHLCV data from yfinance for KOSPI/KOSDAQ stocks.
"""

import yfinance as yf
import pandas as pd
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class PriceCollector:
    """Collects historical price data from yfinance."""

    def __init__(self, period: str = "1y"):
        self.period = period
        self.end_date = datetime.now()
        self.start_date = self.end_date - timedelta(days=365)

    def collect(self, stock: Dict) -> Optional[pd.DataFrame]:
        """
        Download historical data for a single stock.
        
        Args:
            stock: Stock info dict with code, name, market
        
        Returns:
            DataFrame with OHLCV data or None on failure
        """
        code = stock["code"]
        market = stock["market"]
        suffix = ".KS" if market == "KOSPI" else ".KQ"
        ticker_symbol = f"{code}{suffix}"

        try:
            ticker = yf.Ticker(ticker_symbol)
            df = ticker.history(start=self.start_date, end=self.end_date)

            if df.empty:
                logger.warning(f"No data for {code} ({stock['name']})")
                return None

            # Normalize columns
            df = df.reset_index()
            df["stock_code"] = code
            df["stock_name"] = stock["name"]
            df["market"] = market

            # Rename to match DB schema
            df.rename(
                columns={
                    "Date": "date",
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                },
                inplace=True,
            )

            # Add date only
            df["trade_date"] = df["date"].dt.date

            return df

        except Exception as e:
            logger.error(f"Failed to collect {code} ({stock['name']}): {e}")
            return None

    def collect_all(self, stocks: List[Dict]) -> pd.DataFrame:
        """
        Download data for multiple stocks.
        
        Args:
            stocks: List of stock info dicts
        
        Returns:
            Combined DataFrame for all stocks
        """
        all_data = []
        for i, stock in enumerate(stocks):
            logger.info(f"[{i+1}/{len(stocks)}] Collecting {stock['code']} ({stock['name']})")
            df = self.collect(stock)
            if df is not None:
                all_data.append(df)

        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            logger.info(f"Total collected: {len(result)} rows for {len(all_data)} stocks")
            return result

        return pd.DataFrame()
