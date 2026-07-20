import time
import logging
import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class InvestorCollector:
    def __init__(self, market="KOSPI"):
        self.market = market
        self.end_date = datetime.now().strftime("%Y%m%d")
        self.start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    def collect_trading_value(self) -> pd.DataFrame:
        rows = []
        try:
            time.sleep(1)
            df = stock.get_market_trading_value_by_investor(
                self.start_date, self.end_date, self.market
            )
            if df is None or df.empty:
                logger.warning(f"No trading value data for {self.market}")
                return pd.DataFrame()

            df = df.reset_index()
            for _, row in df.iterrows():
                trade_date = row.get("날짜") or row.get("Date") or row.get("날짜")
                if trade_date is None:
                    continue
                if isinstance(trade_date, str):
                    trade_date = datetime.strptime(trade_date, "%Y%m%d").date()
                elif hasattr(trade_date, "date"):
                    trade_date = trade_date.date()

                for col, investor_type in [
                    ("투자자별_거래대금_외국인", "Foreign"),
                    ("투자자별_거래대금_기관", "Institution"),
                    ("투자자별_거래대금_개인", "Individual"),
                    ("투자자별_거래대금_금융투자", "Financial"),
                    ("투자자별_거래대금_보험", "Insurance"),
                    ("투자자별_거래대금_투신", "Trust"),
                    ("투자자별_거래대금_연기금", "Pension"),
                ]:
                    if col in df.columns:
                        val = row.get(col, 0)
                        rows.append({
                            "trade_date": trade_date,
                            "market": self.market,
                            "investor_type": investor_type,
                            "trading_value": int(val) if pd.notna(val) else 0,
                            "net_buy": 0,
                        })
            logger.info(f"Collected {len(df)} days of trading value for {self.market}")
        except Exception as e:
            logger.warning(f"Trading value collection failed for {self.market}: {e}")

        return pd.DataFrame(rows)

    def collect_net_purchases(self) -> pd.DataFrame:
        rows = []
        try:
            time.sleep(1)
            df = stock.get_market_net_purchases_of_equities(
                self.start_date, self.end_date, self.market
            )
            if df is None or df.empty:
                logger.warning(f"No net purchase data for {self.market}")
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

                for col, investor_type in [
                    ("외국인_순매수", "Foreign"),
                    ("기관_순매수", "Institution"),
                    ("개인_순매수", "Individual"),
                    ("금융투자_순매수", "Financial"),
                    ("보험_순매수", "Insurance"),
                    ("투신_순매수", "Trust"),
                    ("연기금_순매수", "Pension"),
                ]:
                    if col in df.columns:
                        val = row.get(col, 0)
                        rows.append({
                            "trade_date": trade_date,
                            "market": self.market,
                            "investor_type": investor_type,
                            "trading_value": 0,
                            "net_buy": int(val) if pd.notna(val) else 0,
                        })
            logger.info(f"Collected {len(df)} days of net purchases for {self.market}")
        except Exception as e:
            logger.warning(f"Net purchase collection failed for {self.market}: {e}")

        return pd.DataFrame(rows)
