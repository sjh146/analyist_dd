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
            # Step 1: Get total market trading value per day
            time.sleep(1)
            df_trading = stock.get_market_trading_value_by_date(
                self.start_date, self.end_date, self.market
            )

            # Step 2: Get foreign investor trading values per day
            time.sleep(1)
            df_investor = stock.get_market_trading_value_by_investor(
                self.start_date, self.end_date, self.market
            )

            # Step 3: For exhaustion rate, iterate over each date
            from pykrx.website.krx.market.wrap import get_exhaustion_rates_of_foreign_investment_by_ticker
            current = datetime.strptime(self.start_date, "%Y%m%d")
            end_dt = datetime.strptime(self.end_date, "%Y%m%d")

            date_exhaustion = {}
            while current <= end_dt:
                try:
                    date_str = current.strftime("%Y%m%d")
                    time.sleep(0.5)
                    df_exhaust = get_exhaustion_rates_of_foreign_investment_by_ticker(date_str, self.market, False)
                    if df_exhaust is not None and not df_exhaust.empty and '보유수량' in df_exhaust.columns:
                        total_held = df_exhaust['보유수량'].sum()
                        total_listed = df_exhaust['상장주식수'].sum()
                        date_exhaustion[date_str] = (total_held / total_listed * 100) if total_listed > 0 else 0.0
                except Exception:
                    pass  # skip non-trading days where pykrx internal logging crashes
                current += timedelta(days=1)

            # Step 4: Combine all data by trade_date
            # df_trading has 날짜 as index (datetime), columns: 기관합계, 기타법인, 개인, 외국인합계, 전체 (net trading values by investor type)
            for trade_date_idx, trow in df_trading.iterrows():
                date_str = trade_date_idx.strftime("%Y%m%d") if hasattr(trade_date_idx, 'strftime') else str(trade_date_idx)

                total_value = int(abs(trow.get('기관합계', 0)) + abs(trow.get('기타법인', 0)) + abs(trow.get('개인', 0)) + abs(trow.get('외국인합계', 0)))

                # Foreign investor data: df_investor has index '투자자구분' with values like '외국인'
                foreign_buy = 0
                foreign_sell = 0
                foreign_net = 0
                if '외국인' in df_investor.index:
                    finfo = df_investor.loc['외국인']
                    foreign_sell = int(finfo.get('매도', 0))
                    foreign_buy = int(finfo.get('매수', 0))
                    foreign_net = int(finfo.get('순매수', 0))

                exhaustion_rate = date_exhaustion.get(date_str, 0.0)

                trade_date = trade_date_idx.date() if hasattr(trade_date_idx, 'date') else trade_date_idx
                rows.append({
                    "trade_date": trade_date,
                    "market": self.market,
                    "total_value": total_value,
                    "foreign_buy_value": foreign_buy,
                    "foreign_sell_value": foreign_sell,
                    "net_buy": foreign_net,
                    "exhaustion_rate": exhaustion_rate,
                })

            logger.info(f"Collected {len(rows)} days of program trading for {self.market}")
        except Exception as e:
            logger.warning(f"Program trading collection failed for {self.market}: {e}")

        return pd.DataFrame(rows)
