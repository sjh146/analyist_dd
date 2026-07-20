import time
import logging
import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class DerivativesCollector:
    INDEX_MAP = {
        "KOSPI200": "KOSPI200",
        "KOSPI": "KOSPI",
        "KOSDAQ": "KOSDAQ",
    }

    def __init__(self, index_name="KOSPI200"):
        self.index_name = index_name
        self.end_date = datetime.now().strftime("%Y%m%d")
        self.start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

    def collect_index_ohlcv(self) -> pd.DataFrame:
        rows = []
        for name in self.INDEX_MAP:
            try:
                time.sleep(1)
                df = stock.get_index_ohlcv(
                    self.start_date, self.end_date, name
                )
                if df is None or df.empty:
                    logger.debug(f"No OHLCV data for index {name}")
                    continue
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
                        "index_name": name,
                        "open": float(row.get("시가", 0) if pd.notna(row.get("시가", 0)) else 0),
                        "high": float(row.get("고가", 0) if pd.notna(row.get("고가", 0)) else 0),
                        "low": float(row.get("저가", 0) if pd.notna(row.get("저가", 0)) else 0),
                        "close": float(row.get("종가", 0) if pd.notna(row.get("종가", 0)) else 0),
                        "volume": int(row.get("거래량", 0) if pd.notna(row.get("거래량", 0)) else 0),
                    })
                logger.info(f"Collected {len(df)} days of OHLCV for {name}")
            except Exception as e:
                logger.warning(f"Index OHLCV collection failed for {name}: {e}")

        return pd.DataFrame(rows)
