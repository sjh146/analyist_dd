"""
Price Collector
Downloads OHLCV data from yfinance for KOSPI/KOSDAQ stocks.
"""

import yfinance as yf
import pandas as pd
import logging
import time
import random
from typing import List, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class PriceCollector:
    """Collects historical price data from yfinance."""

    _REQUEST_DELAY = 15.0
    _BATCH_SIZE = 10
    _RATE_LIMIT_BACKOFF = 120
    _last_rate_limit_time: Optional[datetime] = None
    _cooldown_until: Optional[datetime] = None

    def __init__(self, period: str = "1y"):
        self.period = period
        self.end_date = datetime.now()
        self.start_date = self.end_date - timedelta(days=365)

    @classmethod
    def reset_rate_limit(cls):
        cls._last_rate_limit_time = None
        cls._cooldown_until = None
        logger.info("Rate limit cooldown reset")

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

        now = datetime.now()
        if self._cooldown_until and now < self._cooldown_until:
            remaining = int((self._cooldown_until - now).total_seconds())
            logger.warning(f"Rate limit cooldown active for {code}, {remaining}s remaining — skipping")
            return None

        try:
            ticker = yf.Ticker(ticker_symbol)
            time.sleep(self._REQUEST_DELAY + random.uniform(0, 3))
            df = ticker.history(start=self.start_date, end=self.end_date)
        except Exception as e:
            if "Too Many Requests" in str(e):
                self._last_rate_limit_time = datetime.now()
                logger.warning(f"Rate limited on {code}, waiting {self._RATE_LIMIT_BACKOFF}s and retrying...")
                time.sleep(self._RATE_LIMIT_BACKOFF)
                try:
                    ticker = yf.Ticker(ticker_symbol)
                    df = ticker.history(start=self.start_date, end=self.end_date)
                except Exception as retry_e:
                    logger.error(f"Retry failed for {code} ({stock['name']}): {retry_e}")
                    self._cooldown_until = datetime.now() + timedelta(seconds=300)
                    logger.warning(f"Entering 5-minute cooldown until {self._cooldown_until.isoformat()}")
                    return None
            else:
                logger.error(f"Failed to collect {code} ({stock['name']}): {e}")
                return None

        if df.empty:
            logger.warning(f"No data for {code} ({stock['name']})")
            return None

        df = df.reset_index()
        df["stock_code"] = code
        df["stock_name"] = stock["name"]
        df["market"] = market

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

        df["trade_date"] = df["date"].dt.date

        return df

    def collect_fundamentals(self, stock: Dict) -> Dict:
        """Fetch fundamental data (PER, PBR, ROE, market_cap) from yfinance."""
        code = stock["code"]
        market = stock["market"]
        suffix = ".KS" if market == "KOSPI" else ".KQ"
        ticker_symbol = f"{code}{suffix}"

        result = {"stock_code": code, "market_cap": None, "per": None, "pbr": None, "roe": None}

        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info
            if info:
                result["market_cap"] = info.get("marketCap")
                result["per"] = info.get("trailingPE") or info.get("forwardPE")
                result["pbr"] = info.get("priceToBook")
                result["roe"] = info.get("returnOnEquity")
                shares = info.get("sharesOutstanding")
                logger.info(f"Fundamentals for {code}: PER={result['per']}, PBR={result['pbr']}, ROE={result['roe']}, mcap={result['market_cap']}, shares={shares}")
        except Exception as e:
            logger.warning(f"Failed to collect fundamentals for {code}: {e}")

        return result

    def collect_fundamentals_all(self, stocks: List[Dict]) -> List[Dict]:
        results = []
        for i, stock in enumerate(stocks):
            logger.info(f"[{i+1}/{len(stocks)}] Collecting fundamentals for {stock['code']} ({stock['name']})")
            result = self.collect_fundamentals(stock)
            results.append(result)
            time.sleep(3)
        return results

    def collect_all(self, stocks: List[Dict]) -> pd.DataFrame:
        all_data = []
        batch = stocks[:self._BATCH_SIZE]
        for i, stock in enumerate(batch):
            if i % 10 == 0:
                logger.info(f"[{i+1}/{len(batch)}] Collecting {stock['code']} ({stock['name']})")
            df = self.collect(stock)
            if df is not None:
                all_data.append(df)

        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            logger.info(f"Total collected: {len(result)} rows for {len(all_data)} stocks")
            return result

        return pd.DataFrame()
