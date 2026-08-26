"""Minute-price abstraction for day-trading 30-min window scoring.

Design (plan §5):  minute data (KIS) has been collected into ``minute_bars`` by
``services/kis-collector``.  The scoring core depends only on the
:class:`MinutePriceProvider` interface; swapping the provider from the gap
proxy to :class:`KisMinuteProvider` turns on real 30-min-window scoring with no
change to the scoring body.

- :class:`MinutePriceProvider`  — abstract contract (30-min window price lookup).
- :class:`DailyGapProvider`     — legacy proxy: returns the D+1 open as the
  "30-min price" (gap scoring).  Kept as the default for history compatibility.
- :class:`KisMinuteProvider`    — real 30-min price from ``minute_bars``.
"""

from __future__ import annotations

import abc
import logging

logger = logging.getLogger("day_trading_engine.minute_provider")

# Minutes past D+1 open used as the sell point when real minute bars arrive.
DEFAULT_WINDOW_MINUTES = 30

# KIS 정규장 개장 시각 (HHMMSS) — minute_bars."time" 기준 오프셋의 시작점.
KIS_OPEN_TIME = "090000"


def _hhmmss_plus_minutes(hhmmss: str, delta_min: int) -> str:
    """HHMMSS에 delta_min(음수 가능)을 더한 HHMMSS (24h 순환)."""
    h = int(hhmmss[0:2])
    m = int(hhmmss[2:4])
    s = int(hhmmss[4:6])
    total = (h * 60 + m + int(delta_min)) % (24 * 60)
    return f"{total // 60:02d}{total % 60:02d}{s:02d}"


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


class KisMinuteProvider(MinutePriceProvider):
    """Real 30-min-window price from the KIS ``minute_bars`` table.

    Returns the close of the last 1-min bar at or before
    ``D+1 09:00 + minute_offset``.  ``None`` when the window has not elapsed or
    no minute bars exist for the D+1 session → candidate stays unscored.

    Constructor contract mirrors :class:`DailyGapProvider`:

    - ``prices_or_conn``: a live psycopg2 connection (lazy DB lookup), or
    - a pre-built mapping for DB-free tests — either ``{(code, date): price}``
      (same shape as the gap proxy) or ``{(code, date, target_time): price}``.
    """

    def __init__(self, prices_or_conn=None, *, strict: bool = False,
                 open_time: str = KIS_OPEN_TIME):
        self._data = prices_or_conn
        self._strict = strict
        self._open_time = open_time

    def _target_time(self, minute_offset):
        return _hhmmss_plus_minutes(self._open_time, int(minute_offset))

    def _from_dict(self, stock_code, d_plus_1_date, target):
        if (str(stock_code), str(d_plus_1_date), target) in self._data:
            return self._data[(str(stock_code), str(d_plus_1_date), target)]
        return self._data.get((str(stock_code), str(d_plus_1_date)))

    def _from_db(self, stock_code, d_plus_1_date, target):
        if self._data is None:
            return None
        try:
            cur = self._data.cursor()
            cur.execute(
                'SELECT close_price FROM minute_bars '
                'WHERE stock_code = %s AND trade_date = %s AND "time" <= %s '
                'ORDER BY "time" DESC LIMIT 1',
                (stock_code, d_plus_1_date, target),
            )
            row = cur.fetchone()
            cur.close()
        except Exception as e:
            if self._strict:
                raise
            logger.debug("KisMinuteProvider minute lookup failed: %s", e)
            return None
        if row is None or row[0] is None:
            return None
        return float(row[0])

    def get_minute_price(self, stock_code, d_plus_1_date,
                         minute_offset: int = DEFAULT_WINDOW_MINUTES):
        """Return the D+1 open + ``minute_offset`` price, or ``None``."""
        target = self._target_time(minute_offset)
        if isinstance(self._data, dict):
            return self._from_dict(str(stock_code), str(d_plus_1_date), target)
        return self._from_db(str(stock_code), str(d_plus_1_date), target)
