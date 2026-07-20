import time
import logging
import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class ShortSellingCollector:
    def __init__(self, market="KOSPI"):
        self.market = market
        self.end_date = datetime.now().strftime("%Y%m%d")
        self.start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    def collect_short_selling_status(self):
        rows = []
        try:
            time.sleep(1)
            df = stock.get_shorting_status_by_date(
                self.start_date, self.end_date, self.market
            )
            if df is not None and not df.empty:
                df = df.reset_index()
                for _, row in df.iterrows():
                    trade_date = row.get("날짜") or row.get("Date")
                    if trade_date is None:
                        continue
                    if isinstance(trade_date, str):
                        trade_date = datetime.strptime(trade_date, "%Y%m%d").date()
                    elif hasattr(trade_date, "date"):
                        trade_date = trade_date.date()
                    rows.append({
                        "trade_date": trade_date,
                        "short_volume": int(row.get("공매도_수량", 0) if pd.notna(row.get("공매도_수량", 0)) else 0),
                        "short_value": int(row.get("공매도_금액", 0) if pd.notna(row.get("공매도_금액", 0)) else 0),
                        "total_volume": int(row.get("거래량", 0) if pd.notna(row.get("거래량", 0)) else 0),
                        "short_ratio": float(row.get("공매도_비중", 0) if pd.notna(row.get("공매도_비중", 0)) else 0),
                    })
                logger.info(f"Collected {len(df)} days of short selling status for {self.market}")
        except Exception as e:
            logger.warning(f"Short selling status collection failed for {self.market}: {e}")
        return rows

    def collect_short_selling_by_ticker(self):
        rows = []
        try:
            time.sleep(1)
            df = stock.get_shorting_volume_by_ticker(
                self.start_date, self.end_date, self.market
            )
            if df is not None and not df.empty:
                df = df.reset_index()
                for _, row in df.iterrows():
                    trade_date = row.get("날짜") or row.get("Date")
                    if trade_date is None:
                        continue
                    if isinstance(trade_date, str):
                        trade_date = datetime.strptime(trade_date, "%Y%m%d").date()
                    elif hasattr(trade_date, "date"):
                        trade_date = trade_date.date()
                    stock_code = row.get("종목코드") or row.get("Ticker") or ""
                    stock_name = row.get("종목명") or row.get("Name") or ""
                    rows.append({
                        "trade_date": trade_date,
                        "stock_code": str(stock_code),
                        "stock_name": str(stock_name),
                        "short_volume": int(row.get("공매도_수량", 0) if pd.notna(row.get("공매도_수량", 0)) else 0),
                        "short_value": int(row.get("공매도_금액", 0) if pd.notna(row.get("공매도_금액", 0)) else 0),
                        "total_volume": int(row.get("거래량", 0) if pd.notna(row.get("거래량", 0)) else 0),
                        "short_ratio": float(row.get("공매도_비중", 0) if pd.notna(row.get("공매도_비중", 0)) else 0),
                    })
                logger.info(f"Collected {len(df)} rows of short selling by ticker for {self.market}")
        except Exception as e:
            logger.warning(f"Short selling by ticker collection failed for {self.market}: {e}")
        return rows

    def collect_all(self):
        status_rows = self.collect_short_selling_status()
        ticker_rows = self.collect_short_selling_by_ticker()
        return pd.DataFrame(status_rows + ticker_rows) if (status_rows or ticker_rows) else pd.DataFrame()
