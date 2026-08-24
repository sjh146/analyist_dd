"""Day-trading screener data-provider abstraction.

The screener core only talks to a :class:`MarketDataProvider` protocol.  The
concrete implementations are:

- :class:`DbDailyProvider`  — reads daily OHLCV from PostgreSQL ``market_data``
  (current source).
- :class:`FixtureProvider`  — in-memory fixture data (used by DB-free tests).

To switch to intraday (KIS) data later, provide an implementation whose
``load_lookback`` aggregates minute bars to a daily OHLCV frame (or a frame
with a time column) without changing the screener core.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Protocol, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger("day_trading_engine.providers")

OHLCV_COLUMNS = [
    "stock_code", "trade_date", "open_price", "high_price", "low_price",
    "close_price", "volume", "trading_value",
]


@dataclass(frozen=True)
class StockInfo:
    stock_code: str
    stock_name: str = ""
    sector: str = "Unknown"
    latest_date: str = ""


class MarketDataProvider(Protocol):
    """Data-access contract the screener core depends on."""

    def get_universe(self, min_history: int) -> List[StockInfo]:
        """Return candidate stocks (code, name, sector, latest_date)."""
        ...

    def resolve_signal_date(self, date_str: str | None) -> str:
        """Resolve a YYYY-MM-DD date (or None) to the applicable signal date."""
        ...

    def load_lookback(self, signal_date: str, lookback: int) -> pd.DataFrame:
        """Return per-stock recent ``lookback`` OHLCV rows.

        Returned frame has the :data:`OHLCV_COLUMNS` columns (numeric casts
        applied).  Rows are ordered by ``(stock_code, trade_date)``.
        """
        ...


class DbDailyProvider:
    """PostgreSQL-backed daily OHLCV provider.

    Mirrors the query patterns of ``scripts/close_screener.py``
    (window-function bulk load, date resolution, numeric casts).
    """

    def __init__(self, pg_conn, host: str = "", port: int = 0,
                 dbname: str = "", user: str = "", password: str = ""):
        # pg_conn may be a live psycopg2 connection, or connection params.
        self._pg_conn = pg_conn
        self._params = (host, port, dbname, user, password)

    # ── lazy connection helper ─────────────────────────────────────────
    def _conn(self):
        if self._pg_conn is not None:
            return self._pg_conn
        import psycopg2
        host, port, dbname, user, password = self._params
        return psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user, password=password,
        )

    def get_universe(self, min_history: int = 20) -> List[StockInfo]:
        conn = self._conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT s.stock_code, s.stock_name,
                   COALESCE(s.sector, 'Unknown') AS sector,
                   MAX(md.trade_date)::text AS latest_date
            FROM stocks s
            JOIN market_data md ON s.stock_code = md.stock_code
            WHERE s.market = 'KOSDAQ'
            GROUP BY s.stock_code, s.stock_name, s.sector
            HAVING COUNT(*) >= %s
            ORDER BY s.stock_code
        """, (int(min_history),))
        rows = cur.fetchall()
        cur.close()
        out = []
        for code, name, sector, latest in rows:
            out.append(StockInfo(
                stock_code=str(code), stock_name=str(name),
                sector=str(sector), latest_date=str(latest),
            ))
        return out

    def resolve_signal_date(self, date_str: str | None) -> str:
        conn = self._conn()
        cur = conn.cursor()
        if date_str is None:
            cur.execute(
                "SELECT MAX(trade_date)::text FROM market_data "
                "WHERE trade_date <= CURRENT_DATE"
            )
        else:
            cur.execute(
                "SELECT MAX(trade_date)::text FROM market_data "
                "WHERE trade_date <= %s",
                (date_str,),
            )
        row = cur.fetchone()
        cur.close()
        if row is None or row[0] is None:
            raise ValueError("market_data에 유효한 거래일이 없습니다.")
        return str(row[0])

    def load_lookback(self, signal_date: str, lookback: int = 20) -> pd.DataFrame:
        conn = self._conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT stock_code, trade_date,
                   open_price::float8, high_price::float8, low_price::float8,
                   close_price::float8, volume::float8, trading_value::float8
            FROM (
                SELECT stock_code, trade_date, open_price, high_price,
                       low_price, close_price, volume, trading_value,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code ORDER BY trade_date DESC) AS rn
                FROM market_data
                WHERE trade_date <= %s
            ) t
            WHERE rn <= %s
            ORDER BY stock_code, trade_date
        """, (signal_date, int(lookback)))
        rows = cur.fetchall()
        cur.close()
        df = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
        return _coerce_ohlcv(df)


class FixtureProvider:
    """In-memory provider backed by a pre-built OHLCV frame + meta.

    Enables the screener core to run without a database (tests / offline).
    """

    def __init__(self, df: pd.DataFrame,
                 meta: Sequence[StockInfo] | None = None,
                 signal_date: str = "2026-08-24"):
        self._df = _coerce_ohlcv(df.copy())
        self._meta = meta
        self._signal_date = signal_date

    @property
    def signal_date(self) -> str:
        return self._signal_date

    def get_universe(self, min_history: int = 20) -> List[StockInfo]:
        if self._meta is not None:
            return list(self._meta)
        codes = self._df["stock_code"].unique()
        if min_history > 0:
            counts = self._df.groupby("stock_code").size()
            codes = [c for c in codes if counts.get(c, 0) >= min_history]
        return [
            StockInfo(stock_code=str(c)) for c in codes
        ]

    def resolve_signal_date(self, date_str: str | None) -> str:
        return self._signal_date if date_str is None else str(date_str)

    def load_lookback(self, signal_date: str, lookback: int = 20) -> pd.DataFrame:
        df = self._df.copy()
        if "trade_date" in df.columns:
            df = df[df["trade_date"].astype(str) <= str(signal_date)]
        if lookback and lookback > 0:
            df = (
                df.sort_values(["stock_code", "trade_date"])
                  .groupby("stock_code", as_index=False)
                  .tail(lookback)
            )
        return _coerce_ohlcv(df)


def _coerce_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise an OHLCV frame to the screener's expected dtype/layout."""
    for col in OHLCV_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    if len(df):
        df["stock_code"] = df["stock_code"].astype(str)
        for col in ["open_price", "high_price", "low_price", "close_price",
                    "volume", "trading_value"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    return df[OHLCV_COLUMNS].copy()
