"""
Supply Collector
Collects foreign/institutional supply data from yfinance.
Note: yfinance institutional_holders returns quarterly snapshots, not daily data.
"""

import yfinance as yf
import pandas as pd
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class SupplyCollector:
    """Collects foreign/institutional/individual supply data for stocks."""

    def collect_supply_data(self, stock_codes: List[str]) -> pd.DataFrame:
        """
        Collect institutional holder data for multiple stocks.

        Args:
            stock_codes: List of stock codes (e.g. ['005930'])

        Returns:
            DataFrame with columns: stock_code, trade_date,
            foreign_net_buy, institution_net_buy, individual_net_buy
        """
        rows = []

        for code in stock_codes:
            try:
                ticker = yf.Ticker(f"{code}.KS")
                holders = ticker.institutional_holders

                if holders is None or holders.empty:
                    ticker_kq = yf.Ticker(f"{code}.KQ")
                    holders = ticker_kq.institutional_holders

                if holders is None or holders.empty:
                    logger.debug(f"No institutional holder data for {code}")
                    continue

                total_shares = ticker.info.get("sharesOutstanding", 0)

                for _, row_data in holders.iterrows():
                    pct = float(row_data.get("% Out", 0)) if pd.notna(row_data.get("% Out", 0)) else 0
                    shares = float(row_data.get("Shares", 0)) if pd.notna(row_data.get("Shares", 0)) else 0
                    report_date = row_data.get("Date Reported", None)

                    if report_date is None:
                        continue

                    holder_name = str(row_data.get("Holder", "")).lower()

                    is_foreign = any(kw in holder_name for kw in
                        ["foreign", "global", "international", "overseas",
                         "vanguard", "blackrock", "fidelity", "capital",
                         "외국", "해외"])
                    is_institution = any(kw in holder_name for kw in
                        ["bank", "insurance", "pension", "fund", "asset",
                         "trust", "securities", "invest",
                         "은행", "보험", "연금", "증권", "자산", "투신"])

                    rows.append({
                        "stock_code": code,
                        "trade_date": pd.Timestamp(report_date).date()
                            if not isinstance(report_date, pd.Timestamp) else report_date.date(),
                        "holder_name": row_data.get("Holder", ""),
                        "shares_held": shares,
                        "pct_outstanding": pct,
                        "is_foreign": is_foreign,
                        "is_institution": is_institution,
                    })

            except Exception as e:
                logger.debug(f"Supply data collection failed for {code}: {e}")
                continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df.sort_values(["stock_code", "trade_date"])

        return df

    def aggregate_to_daily(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate holder data to daily net buy estimates.
        Uses changes in shares held between reporting periods as proxy.
        """
        if df.empty:
            return df

        result_rows = []
        for code, group in df.groupby("stock_code"):
            group = group.sort_values("trade_date")

            foreign_shares = group[group["is_foreign"]].groupby("trade_date")["shares_held"].sum()
            inst_shares = group[group["is_institution"]].groupby("trade_date")["shares_held"].sum()
            total_shares = group.groupby("trade_date")["shares_held"].sum()
            individual_shares = total_shares - foreign_shares - inst_shares

            foreign_diff = foreign_shares.diff().fillna(0)
            inst_diff = inst_shares.diff().fillna(0)
            individual_diff = individual_shares.diff().fillna(0)

            for date_val in foreign_shares.index:
                result_rows.append({
                    "stock_code": code,
                    "trade_date": date_val,
                    "foreign_net_buy": float(foreign_diff.get(date_val, 0)),
                    "institution_net_buy": float(inst_diff.get(date_val, 0)),
                    "individual_net_buy": float(individual_diff.get(date_val, 0)),
                })

        return pd.DataFrame(result_rows)
