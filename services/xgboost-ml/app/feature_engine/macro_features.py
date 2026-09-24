"""
Macro Features
Extracts features from macro-economic indicator data (Bank of Korea ECOS / FRED).

Two modes:

* **as-of mode** (``date`` given) - every feature is built from observations that
  are on or before the requested date, so a row for 2026-04-15 only ever sees
  information that was public on 2026-04-15 (no look-ahead bias).
* **legacy mode** (``date is None``) - the historical behaviour: the four most
  recent rows of each indicator, which are identical for every training row.

The full indicator table is loaded once per process/TTL and cached on the
instance; ``invalidate_cache()`` (or ``MACRO_FEATURE_CACHE_TTL``) refreshes it.
"""

import bisect
import logging
import os
import threading
import time
from datetime import date as _date, datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature contract: exactly these 13 keys are produced by this reader.
# ---------------------------------------------------------------------------
MACRO_FEATURE_KEYS = (
    "interest_rate", "interest_rate_change_1m", "interest_rate_change_3m",
    "fx_usd_krw", "fx_change_1m", "fx_change_3m",
    "oil_wti", "oil_change_1m", "oil_change_3m",
    "cpi_yoy", "ppi_yoy",
    "yield_spread", "credit_spread",
)

# Indicator names as stored in macro_indicators (ECOS naming).
BASE_RATE = "기준금리"
USD_KRW = "USD/KRW 환율"
JPY_KRW = "JPY/KRW 환율"
CNY_KRW = "CNY/KRW 환율"
WTI = "WTI 유가"
CPI = "CPI"
PPI = "PPI"
GOV_3Y = "국고채3년"
CORP_3Y = "회사채3년"

# Indicators this reader actually consumes (rest of the table is ignored).
READER_INDICATORS = (BASE_RATE, USD_KRW, WTI, CPI, PPI, GOV_3Y, CORP_3Y)

# Lookback windows (calendar days) for the change-rate features.
_ONE_MONTH = 30
_THREE_MONTHS = 90
_TWELVE_MONTHS = 365  # YoY(전년동월비) 계산용

# Default series-cache lifetime in seconds; 0 disables expiry (manual only).
DEFAULT_CACHE_TTL = 300.0


def _as_date(value) -> Optional[_date]:
    """Normalise str/date/datetime to a ``datetime.date``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.debug("macro_features: unparsable date %r", value)
        return None


class MacroFeatures:
    """Features derived from macro-economic indicators: rates, FX, oil, inflation."""

    def __init__(self, cache_ttl: Optional[float] = None):
        self._series_cache: Optional[Dict[str, list]] = None
        self._series_dates: Dict[str, list] = {}
        self._cache_loaded_at: float = 0.0
        self._cache_lock = threading.Lock()
        if cache_ttl is None:
            try:
                cache_ttl = float(os.environ.get("MACRO_FEATURE_CACHE_TTL", DEFAULT_CACHE_TTL))
            except (TypeError, ValueError):
                cache_ttl = DEFAULT_CACHE_TTL
        self._cache_ttl = float(cache_ttl)

    # ------------------------------------------------------------------
    # cache handling
    # ------------------------------------------------------------------
    def invalidate_cache(self) -> None:
        """Drop the cached indicator series (call after a macro backfill)."""
        with self._cache_lock:
            self._series_cache = None
            self._series_dates = {}
            self._cache_loaded_at = 0.0

    def _cache_is_fresh(self) -> bool:
        if self._series_cache is None:
            return False
        if self._cache_ttl <= 0:
            return True
        return (time.monotonic() - self._cache_loaded_at) < self._cache_ttl

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def get_macro_from_db(self, db_conn=None, date=None) -> Dict:
        """Fetch macro indicators from PostgreSQL and compute features.

        Args:
            db_conn: open PostgreSQL connection (None -> all-zero features).
            date:    as-of date (``str``/``date``/``datetime``). When given, only
                     observations dated on or before it are used. When ``None``
                     the legacy "latest 4 rows" behaviour is preserved.
        """
        features = {key: 0.0 for key in MACRO_FEATURE_KEYS}

        asof = _as_date(date)
        if db_conn is None and not self._cache_is_fresh():
            return features

        try:
            series = self._load_series(db_conn)
            if not series:
                return features
            if asof is not None:
                features.update(self._compute_asof_features(series, asof))
            else:
                features.update(self._compute_features(self._legacy_window(series)))
        except Exception as e:
            logger.debug(f"Macro features failed: {e}")
            if db_conn is not None:
                try:
                    db_conn.rollback()
                except Exception:
                    pass

        return features

    def get_all_features(self, db_conn=None, date=None) -> Dict:
        """Get all macro-economic features (see ``get_macro_from_db``)."""
        return self.get_macro_from_db(db_conn, date)

    # ------------------------------------------------------------------
    # series loading / caching
    # ------------------------------------------------------------------
    def _load_series(self, db_conn=None) -> Dict[str, list]:
        """Load the full indicator table once, ascending by date, and cache it."""
        if self._cache_is_fresh():
            return self._series_cache or {}

        with self._cache_lock:
            if self._cache_is_fresh():
                return self._series_cache or {}
            if db_conn is None:
                return self._series_cache or {}
            series = self._fetch_series(db_conn)
            if series:
                self._series_cache = series
                self._series_dates = {
                    name: [d for d, _ in rows] for name, rows in series.items()
                }
                self._cache_loaded_at = time.monotonic()
            return series

    def _fetch_series(self, db_conn) -> Dict[str, list]:
        """Read the whole macro_indicators table and group it by indicator name."""
        cur = db_conn.cursor()
        try:
            cur.execute("""
                SELECT indicator_name, date, value
                FROM macro_indicators
                ORDER BY indicator_name, date ASC
            """)
            rows = cur.fetchall()
        finally:
            cur.close()

        by_name: Dict[str, list] = {}
        for name, obs_date, val in rows:
            obs = _as_date(obs_date)
            if obs is None:
                continue
            by_name.setdefault(name, []).append(
                (obs, float(val) if val is not None else 0.0)
            )

        for name in by_name:
            by_name[name].sort(key=lambda item: item[0])

        return by_name

    @staticmethod
    def _legacy_window(series: Dict[str, list]) -> Dict[str, list]:
        """Back-compat view: newest-first, at most 4 rows per indicator."""
        return {
            name: list(reversed(rows[-4:]))
            for name, rows in series.items()
            if rows
        }

    # ------------------------------------------------------------------
    # as-of computation (no look-ahead)
    # ------------------------------------------------------------------
    @staticmethod
    def _asof_value(rows: Optional[list], asof: _date, days_back: int = 0,
                    date_keys: Optional[list] = None):
        """Latest value observed on or before ``asof - days_back``."""
        if not rows:
            return None
        cutoff = asof - timedelta(days=days_back)
        if date_keys and len(date_keys) == len(rows):
            idx = bisect.bisect_right(date_keys, cutoff) - 1
            return rows[idx][1] if idx >= 0 else None
        value = None
        for obs_date, val in rows:  # ascending
            if obs_date <= cutoff:
                value = val
            else:
                break
        return value

    def _anchor(self, series: Dict[str, list], name: str, asof: _date, days_back: int):
        return self._asof_value(
            series.get(name), asof, days_back, self._series_dates.get(name)
        )

    def _pct_change(self, series, name, asof, days_back):
        now = self._anchor(series, name, asof, 0)
        then = self._anchor(series, name, asof, days_back)
        if now is None or not then:
            return 0.0
        return (now - then) / then * 100.0

    def _diff_change(self, series, name, asof, days_back):
        now = self._anchor(series, name, asof, 0)
        then = self._anchor(series, name, asof, days_back)
        if now is None or then is None:
            return 0.0
        return now - then

    def _compute_asof_features(self, series: Dict[str, list], asof: _date) -> Dict:
        """Compute the 13 macro features using only data available on ``asof``."""
        feats: Dict[str, float] = {}

        def put(key, value):
            if value is not None:
                feats[key] = float(value)

        put("interest_rate", self._anchor(series, BASE_RATE, asof, 0))
        put("interest_rate_change_1m", self._diff_change(series, BASE_RATE, asof, _ONE_MONTH))
        put("interest_rate_change_3m", self._diff_change(series, BASE_RATE, asof, _THREE_MONTHS))

        put("fx_usd_krw", self._anchor(series, USD_KRW, asof, 0))
        put("fx_change_1m", self._pct_change(series, USD_KRW, asof, _ONE_MONTH))
        put("fx_change_3m", self._pct_change(series, USD_KRW, asof, _THREE_MONTHS))

        put("oil_wti", self._anchor(series, WTI, asof, 0))
        put("oil_change_1m", self._pct_change(series, WTI, asof, _ONE_MONTH))
        put("oil_change_3m", self._pct_change(series, WTI, asof, _THREE_MONTHS))

        # YoY 는 **증감률**이어야 한다. 종전엔 지수 레벨을 그대로 넣어(실측 2026-09-24:
        # cpi_yoy=120.05, ppi_yoy=129.64) 이름과 값이 불일치하고, 비정상(non-stationary)
        # 시계열이라 모델에는 시간 추세로만 작동한다.
        put("cpi_yoy", self._pct_change(series, CPI, asof, _TWELVE_MONTHS))
        put("ppi_yoy", self._pct_change(series, PPI, asof, _TWELVE_MONTHS))

        gov = self._anchor(series, GOV_3Y, asof, 0)
        base = self._anchor(series, BASE_RATE, asof, 0)
        if gov is not None and base is not None:
            # 장단기/정책금리 스프레드(국고채3년 − 기준금리). 종전엔 하드코딩 상수 3.5 를
            # 기준선으로 써서 실질적으로 레벨을 그대로 넣는 것과 같았다.
            put("yield_spread", gov - base)
        corp = self._anchor(series, CORP_3Y, asof, 0)
        if corp is not None and gov is not None:
            put("credit_spread", corp - gov)

        return feats

    # ------------------------------------------------------------------
    # legacy computation (date=None) - unchanged behaviour
    # ------------------------------------------------------------------
    def _compute_features(self, indicators: Dict) -> Dict:
        """Compute derived features from indicator time-series (legacy path)."""
        features = {}

        for name, compute_fn in [
            (BASE_RATE, self._rate_features),
            (USD_KRW, self._fx_features),
            (WTI, self._oil_features),
            (CPI, self._inflation_features),
            (PPI, self._inflation_features),
            (GOV_3Y, self._bond_features),
        ]:
            series = indicators.get(name, [])
            if series:
                features.update(compute_fn(name, series))

        if "국고채3년" in features and CORP_3Y in indicators:
            corp_series = indicators.get(CORP_3Y, [])
            gov_series = indicators.get(GOV_3Y, [])
            if corp_series and gov_series:
                features["credit_spread"] = corp_series[0][1] - gov_series[0][1]

        return features

    def _rate_features(self, name: str, series: list) -> Dict:
        vals = [s[1] for s in series]
        feat = {"interest_rate": vals[0]}
        feat["interest_rate_change_1m"] = vals[0] - vals[-1] if len(vals) >= 2 else 0.0
        feat["interest_rate_change_3m"] = vals[0] - vals[-1] if len(vals) >= 3 else 0.0
        return feat

    def _fx_features(self, name: str, series: list) -> Dict:
        vals = [s[1] for s in series]
        feat = {"fx_usd_krw": vals[0]}
        feat["fx_change_1m"] = (vals[0] - vals[-1]) / vals[-1] * 100 if len(vals) >= 2 and vals[-1] else 0.0
        feat["fx_change_3m"] = (vals[0] - vals[-1]) / vals[-1] * 100 if len(vals) >= 3 and vals[-1] else 0.0
        return feat

    def _oil_features(self, name: str, series: list) -> Dict:
        vals = [s[1] for s in series]
        feat = {"oil_wti": vals[0]}
        feat["oil_change_1m"] = (vals[0] - vals[-1]) / vals[-1] * 100 if len(vals) >= 2 and vals[-1] else 0.0
        feat["oil_change_3m"] = (vals[0] - vals[-1]) / vals[-1] * 100 if len(vals) >= 3 and vals[-1] else 0.0
        return feat

    def _inflation_features(self, name: str, series: list) -> Dict:
        vals = [s[1] for s in series]
        suffix = name.lower()
        feat = {}
        feat[f"{suffix}_yoy"] = vals[0]
        return feat

    def _bond_features(self, name: str, series: list) -> Dict:
        vals = [s[1] for s in series]
        feat = {}
        if "yield_spread" not in feat:
            base_rate = 3.5
            feat["yield_spread"] = vals[0] - base_rate
        return feat
