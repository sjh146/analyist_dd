"""
Price Collector
Downloads OHLCV data from pykrx for KOSPI/KOSDAQ stocks.
"""

import pandas as pd
import logging
import time
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from pykrx import stock as krx_stock

logger = logging.getLogger(__name__)


class PriceCollector:
    """Collects historical price data from pykrx."""

    def __init__(self, period: str = "1y"):
        self.period = period
        self.end_date = datetime.now()
        self.start_date = self.end_date - timedelta(days=365)

    def collect(self, stock: Dict) -> Optional[pd.DataFrame]:
        code = stock["code"]
        market = stock["market"]

        try:
            df = krx_stock.get_market_ohlcv_by_date(
                self.start_date.strftime("%Y%m%d"),
                self.end_date.strftime("%Y%m%d"),
                code
            )

            if df.empty:
                logger.warning(f"No data for {code} ({stock['name']})")
                return None

            df = df.reset_index()
            df["stock_code"] = code
            df["stock_name"] = stock["name"]
            df["market"] = market

            df.rename(columns={
                "날짜": "date",
                "시가": "open",
                "고가": "high",
                "저가": "low",
                "종가": "close",
                "거래량": "volume",
            }, inplace=True)

            df["trade_date"] = pd.to_datetime(df["date"]).dt.date
            df["open"] = df["open"].astype(float)
            df["high"] = df["high"].astype(float)
            df["low"] = df["low"].astype(float)
            df["close"] = df["close"].astype(float)
            df["volume"] = df["volume"].astype(int)

            return df

        except Exception as e:
            logger.error(f"Failed to collect {code} ({stock['name']}): {e}")
            return None

    def collect_fundamentals(self, stock: Dict) -> Dict:
        code = stock["code"]
        result = {"stock_code": code, "market_cap": None, "per": None, "pbr": None, "roe": None}

        try:
            df = krx_stock.get_market_fundamental_by_date(
                self.end_date.strftime("%Y%m%d"),
                self.end_date.strftime("%Y%m%d"),
                code
            )
            if not df.empty:
                latest = df.iloc[-1]
                result["per"] = float(latest.get("PER", 0)) if pd.notna(latest.get("PER")) else None
                result["pbr"] = float(latest.get("PBR", 0)) if pd.notna(latest.get("PBR")) else None
                result["roe"] = float(latest.get("EPS", 0)) if pd.notna(latest.get("EPS")) else None

                ohlcv = krx_stock.get_market_ohlcv_by_date(
                    self.end_date.strftime("%Y%m%d"),
                    self.end_date.strftime("%Y%m%d"),
                    code
                )
                if not ohlcv.empty:
                    close_price = float(ohlcv.iloc[-1].get("종가", 0))
                    shares = krx_stock.get_stock_share_info(code, self.end_date.strftime("%Y%m%d"))
                    result["market_cap"] = int(close_price * shares) if shares else None

        except Exception as e:
            logger.warning(f"Failed to collect fundamentals for {code}: {e}")

        return result

    def collect_fundamentals_all(self, stocks: List[Dict]) -> List[Dict]:
        results = []
        for i, stock in enumerate(stocks):
            logger.info(f"[{i+1}/{len(stocks)}] Collecting fundamentals for {stock['code']} ({stock['name']})")
            result = self.collect_fundamentals(stock)
            results.append(result)
            time.sleep(0.3)
        return results

    def collect_all(self, stocks: List[Dict]) -> pd.DataFrame:
        all_data = []
        for i, stock in enumerate(stocks):
            if i > 0 and i % 200 == 0:
                try:
                    from pykrx import stock as krx_stock
                    krx_stock.reconnect()
                    logger.info(f"Reconnected pykrx at stock {i}")
                except Exception as e:
                    logger.warning(f"pykrx reconnect failed: {e}")
            if i % 10 == 0:
                logger.info(f"[{i+1}/{len(stocks)}] Collecting {stock['code']} ({stock['name']})")
            df = self.collect(stock)
            if df is not None:
                all_data.append(df)
            time.sleep(0.3)

        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            logger.info(f"Total collected: {len(result)} rows for {len(all_data)} stocks")
            return result

        return pd.DataFrame()
