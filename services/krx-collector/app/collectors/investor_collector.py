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
            df = stock.get_market_trading_value_by_date(
                self.start_date, self.end_date, self.market
            )
            if df is None or df.empty:
                logger.warning(f"No trading value data for {self.market}")
                return pd.DataFrame()

            INVESTOR_TYPE_MAP = {
                "기관합계": "Institution",
                "기타법인": "OtherCorp",
                "개인": "Individual",
                "외국인합계": "Foreign",
            }

            df = df.reset_index()
            for _, row in df.iterrows():
                trade_date = row.get("날짜")
                if trade_date is None:
                    continue
                if isinstance(trade_date, str):
                    trade_date = datetime.strptime(trade_date, "%Y%m%d").date()
                elif hasattr(trade_date, "date"):
                    trade_date = trade_date.date()

                for col, investor_type in INVESTOR_TYPE_MAP.items():
                    if col in df.columns:
                        val = row.get(col, 0)
                        rows.append({
                            "trade_date": trade_date,
                            "market": self.market,
                            "investor_type": investor_type,
                            "trading_value": 0,
                            "net_buy": int(val) if pd.notna(val) else 0,
                        })
            logger.info(f"Collected {len(df)} days of trading value for {self.market}")
        except Exception as e:
            logger.warning(f"Trading value collection failed for {self.market}: {e}")

        return pd.DataFrame(rows)

    def collect_net_purchases(self) -> pd.DataFrame:
        rows = []
        try:
            time.sleep(1)
            df = stock.get_market_trading_value_by_investor(
                self.start_date, self.end_date, self.market
            )
            if df is None or df.empty:
                logger.warning(f"No net purchase data for {self.market}")
                return pd.DataFrame()

            INVESTOR_TYPE_MAP = {
                "금융투자": "Financial",
                "보험": "Insurance",
                "투신": "Trust",
                "사모": "PrivateEquity",
                "은행": "Bank",
                "기타금융": "OtherFinancial",
                "연기금 등": "Pension",
                "기관합계": "Institution",
                "기타법인": "OtherCorp",
                "개인": "Individual",
                "외국인": "Foreign",
                "기타외국인": "OtherForeign",
                "전체": "Total",
            }

            for investor_kr, investor_en in INVESTOR_TYPE_MAP.items():
                if investor_kr in df.index:
                    buy_val = df.loc[investor_kr, "매수"]
                    sell_val = df.loc[investor_kr, "매도"]
                    net_val = df.loc[investor_kr, "순매수"]
                    rows.append({
                        "trade_date": datetime.now().date(),
                        "market": self.market,
                        "investor_type": investor_en,
                        "trading_value": int(buy_val) if pd.notna(buy_val) else 0,
                        "net_buy": int(net_val) if pd.notna(net_val) else 0,
                    })
            logger.info(f"Collected {len(rows)} investor net purchase rows for {self.market}")
        except Exception as e:
            logger.warning(f"Net purchase collection failed for {self.market}: {e}")

        return pd.DataFrame(rows)
