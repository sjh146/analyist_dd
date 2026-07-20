import time
import logging
import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class ProgramTradingCollector:
    def __init__(self, market="KOSPI"):
        self.market = market
        self.end_date = datetime.now().strftime("%Y%m%d")
        self.start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    def collect_exhaustion_rates(self) -> pd.DataFrame:
        rows = []
        try:
            time.sleep(1)
            df = stock.get_exhaustion_rates_of_foreign_investment(
                self.start_date, self.end_date, self.market
            )
            if df is None or df.empty:
                logger.warning(f"No exhaustion rate data for {self.market}")
                return pd.DataFrame()

            df = df.reset_index()
            for _, row in df.iterrows():
                trade_date = row.get("날짜") or row.get("Date")
                if trade_date is None:
                    continue
                if isinstance(trade_date, str):
                    trade_date = datetime.strptime(trade_date, "%Y%m%d").date()
                elif hasattr(trade_date, "date"):
                    trade_date = trade_date.date()

                total_val = row.get("전체", 0)
                if pd.isna(total_val):
                    total_val = 0
                rows.append({
                    "trade_date": trade_date,
                    "market": self.market,
                    "total_value": int(total_val),
                    "foreign_buy_value": int(row.get("매수", 0) if pd.notna(row.get("매수", 0)) else 0),
                    "foreign_sell_value": int(row.get("매도", 0) if pd.notna(row.get("매도", 0)) else 0),
                    "net_buy": int(row.get("순매수", 0) if pd.notna(row.get("순매수", 0)) else 0),
                    "exhaustion_rate": float(row.get("소진율", 0) if pd.notna(row.get("소진율", 0)) else 0),
                })
            logger.info(f"Collected {len(df)} days of program trading for {self.market}")
        except Exception as e:
            logger.warning(f"Program trading collection failed for {self.market}: {e}")

        return pd.DataFrame(rows)
