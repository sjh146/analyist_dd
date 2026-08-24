"""Minute-price abstraction for day-trading 30-min window scoring.

Design (plan §5):  minute data (KIS) is not yet available, so the **current**
implementation scores the D → D+1 open gap as a proxy.  The scoring core depends
only on the :class:`MinutePriceProvider` interface; once KIS minute bars arrive,
adding a ``KisMinuteProvider`` implementation switches scoring to the real 30-min
window with no change to the scoring body.

- :class:`MinutePriceProvider`  — abstract contract (30-min window price lookup).
- :class:`DailyGapProvider`     — current proxy: returns the D+1 open as the
  "30-min price", i.e. gap-up gap scoring.  No minute bars available yet.
"""

from __future__ import annotations

import abc
import logging

logger = logging.getLogger("day_trading_engine.minute_provider")

# Minutes past D+1 open used as the sell point when real minute bars arrive.
DEFAULT_WINDOW_MINUTES = 30


class MinutePriceProvider(abc.ABC):
    """Abstract minute-price access used for the day-trading 30-min window.

    Implementations are expected to be DB-agnostic *price lookups*: given a
    stock and the D+1 trade date, return the price ``minute_offset`` minutes
    after the D+1 open (or ``None`` when the window has not elapsed / no data).
    """

    @abc.abstractmethod
    def get_minute_price(self, stock_code: str, d_plus_1_date,
                         minute_offset: int = DEFAULT_WINDOW_MINUTES):
        """Return the D+1 open + ``minute_offset`` minutes price, else ``None``."""


class DailyGapProvider(MinutePriceProvider):
    """Current proxy: minute bars not yet issued (KIS pending).

    Uses the D+1 *open* as a stand-in for the 30-min price, so the window is
    scored as the gap ``D close → D+1 open``.  Once :class:`KisMinuteProvider`
    is added, this proxy is replaced by swapping the provider only.
    """

    def __init__(self, prices_or_conn=None, *, strict: bool = False):
        # Accept either a live psycopg2 connection (lazy) or a pre-built lookup
        # mapping {(code, date): open_price} for DB-free tests.
        self._data = prices_or_conn
        self._strict = strict

    def _open_price(self, stock_code, d_plus_1_date):
        """Best-effort D+1 open price from DB or injected lookup."""
        if isinstance(self._data, dict):
            return self._data.get((str(stock_code), str(d_plus_1_date)))
        return self._db_open(stock_code, d_plus_1_date)

    def _db_open(self, stock_code, d_plus_1_date):
        if self._data is None:
            return None
        try:
            cur = self._data.cursor()
            cur.execute(
                "SELECT open_price FROM market_data "
                "WHERE stock_code = %s AND trade_date = %s",
                (stock_code, d_plus_1_date),
            )
            row = cur.fetchone()
            cur.close()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("DailyGapProvider open lookup failed: %s", e)
            return None
        if row is None or row[0] is None:
            return None
        return float(row[0])

    def get_minute_price(self, stock_code, d_plus_1_date,
                         minute_offset: int = DEFAULT_WINDOW_MINUTES):
        """Return the D+1 open as the proxy 30-min price, or ``None``.

        ``minute_offset`` is accepted for interface parity and ignored by this
        proxy (no minute bars yet).  Returning ``None`` means the D+1 session is
        not yet priced → window not elapsed, so the candidate is not scored.
        """
        return self._open_price(str(stock_code), d_plus_1_date)
