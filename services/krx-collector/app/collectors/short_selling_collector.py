import time
import logging
import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_BALANCE_COLUMN_ALIASES = {
    "short_volume": ("공매도잔고", "BAL_QTY"),
    "short_value": ("공매도금액", "BAL_AMT"),
    "total_volume": ("상장주식수", "LIST_SHRS"),
    "short_ratio": ("비중", "BAL_RTO"),
}


def _row_value(row, aliases):
    for name in aliases:
        if name in row.index:
            return row[name]
    return None


def _safe_number(value, converter, default=0):
    try:
        if value is None or pd.isna(value):
            return default
        if isinstance(value, str):
            value = value.replace(",", "")
        return converter(value)
    except (TypeError, ValueError):
        return default


class ShortSellingCollector:
    def __init__(self, market="KOSPI"):
        self.market = market
        self.end_date = datetime.now().strftime("%Y%m%d")
        self.start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    def collect_short_selling_status(self):
        rows = []
        time.sleep(1)
        df_short_val = stock.get_shorting_investor_value_by_date(
            self.start_date, self.end_date, self.market
        )
        time.sleep(1)
        df_short_vol = stock.get_shorting_investor_volume_by_date(
            self.start_date, self.end_date, self.market
        )
        time.sleep(1)
        df_total_val = stock.get_market_trading_value_by_date(
            self.start_date, self.end_date, self.market
        )
        time.sleep(1)
        df_total_vol = stock.get_market_trading_volume_by_date(
            self.start_date, self.end_date, self.market
        )

        for date_idx in df_short_val.index:
            try:
                trade_date = date_idx.date() if hasattr(date_idx, 'date') else date_idx
                short_value = int(df_short_val.loc[date_idx, '합계'])
                short_volume = int(df_short_vol.loc[date_idx, '합계'])

                total_value = 0
                total_volume = 0
                if date_idx in df_total_val.index:
                    tv = df_total_val.loc[date_idx]
                    total_value = int(tv.get('매도거래대금', 0) + tv.get('매수거래대금', 0))
                if date_idx in df_total_vol.index:
                    tv2 = df_total_vol.loc[date_idx]
                    total_volume = int(tv2.get('매도거래량', 0) + tv2.get('매수거래량', 0))

                short_ratio = round(short_value / total_value * 100, 2) if total_value > 0 else 0.0

                rows.append({
                    "trade_date": trade_date,
                    "short_volume": short_volume,
                    "short_value": short_value,
                    "total_volume": total_volume,
                    "short_ratio": short_ratio,
                })
            except Exception:
                pass  # skip non-trading days
        logger.info(f"Collected {len(rows)} days of short selling status for {self.market}")
        return rows

    def collect_short_selling_by_ticker(self):
        rows = []
        from pykrx.website.krx.market.wrap import get_shorting_balance_by_ticker
        current = datetime.strptime(self.start_date, "%Y%m%d")
        end_dt = datetime.strptime(self.end_date, "%Y%m%d")
        while current <= end_dt:
            if current.weekday() >= 5:
                logger.debug("Skipping %s (%s): weekend, KRX market closed", current.date(), self.market)
                current += timedelta(days=1)
                continue
            date_str = current.strftime("%Y%m%d")
            try:
                time.sleep(0.5)
                df = get_shorting_balance_by_ticker(date_str, self.market)
                if df is None or df.empty:
                    logger.debug("No short selling balance data for %s (%s)", current.date(), self.market)
                elif not any(
                    col in df.columns
                    for aliases in _BALANCE_COLUMN_ALIASES.values()
                    for col in aliases
                ):
                    logger.debug(
                        "Unexpected columns %s on %s (%s), skipping",
                        list(df.columns), current.date(), self.market,
                    )
                else:
                    for ticker_code, srow in df.iterrows():
                        rows.append({
                            "trade_date": current.date(),
                            "stock_code": str(ticker_code),
                            "stock_name": "",
                            "short_volume": _safe_number(_row_value(srow, _BALANCE_COLUMN_ALIASES["short_volume"]), int),
                            "short_value": _safe_number(_row_value(srow, _BALANCE_COLUMN_ALIASES["short_value"]), int),
                            "total_volume": _safe_number(_row_value(srow, _BALANCE_COLUMN_ALIASES["total_volume"]), int),
                            "short_ratio": _safe_number(_row_value(srow, _BALANCE_COLUMN_ALIASES["short_ratio"]), float),
                        })
            except Exception as e:
                logger.debug("Skipping %s (%s): %s", current.date(), self.market, e)
            current += timedelta(days=1)
        logger.info(f"Collected {len(rows)} rows of short selling by ticker for {self.market}")
        return rows

    def collect_all(self):
        status_rows = self.collect_short_selling_status()
        ticker_rows = self.collect_short_selling_by_ticker()
        return pd.DataFrame(status_rows + ticker_rows) if (status_rows or ticker_rows) else pd.DataFrame()
