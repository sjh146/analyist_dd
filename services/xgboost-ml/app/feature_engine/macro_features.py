"""
Macro Features
Extracts features from macro-economic indicator data (Bank of Korea ECOS).
"""

import logging
import numpy as np
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class MacroFeatures:
    """Features derived from macro-economic indicators: rates, FX, oil, inflation."""

    def get_macro_from_db(self, db_conn=None) -> Dict:
        """Fetch macro indicators from PostgreSQL and compute features."""
        features = {
            "interest_rate": 0.0, "interest_rate_change_1m": 0.0,
            "interest_rate_change_3m": 0.0,
            "fx_usd_krw": 0.0, "fx_change_1m": 0.0, "fx_change_3m": 0.0,
            "oil_wti": 0.0, "oil_change_1m": 0.0, "oil_change_3m": 0.0,
            "cpi_yoy": 0.0, "ppi_yoy": 0.0,
            "yield_spread": 0.0, "credit_spread": 0.0,
        }

        if db_conn is None:
            return features

        try:
            indicators_raw = self._fetch_indicators(db_conn)
            features.update(self._compute_features(indicators_raw))
        except Exception as e:
            logger.debug(f"Macro features failed: {e}")

        return features

    def _fetch_indicators(self, db_conn) -> Dict:
        """Fetch raw macro indicator values from DB."""
        cur = db_conn.cursor()
        cur.execute("""
            SELECT indicator_name, date, value
            FROM (
                SELECT indicator_name, date, value,
                       ROW_NUMBER() OVER (
                           PARTITION BY indicator_name ORDER BY date DESC
                       ) as rn
                FROM macro_indicators
            ) ranked
            WHERE rn <= 4
            ORDER BY indicator_name, date DESC
        """)
        rows = cur.fetchall()
        cur.close()

        by_name = {}
        for name, date, val in rows:
            if name not in by_name:
                by_name[name] = []
            by_name[name].append((date, float(val) if val else 0.0))

        return by_name

    def _compute_features(self, indicators: Dict) -> Dict:
        """Compute derived features from indicator time-series."""
        features = {}

        for name, compute_fn in [
            ("기준금리", self._rate_features),
            ("USD/KRW 환율", self._fx_features),
            ("WTI 유가", self._oil_features),
            ("CPI", self._inflation_features),
            ("PPI", self._inflation_features),
            ("국고채3년", self._bond_features),
        ]:
            series = indicators.get(name, [])
            if series:
                features.update(compute_fn(name, series))

        if "국고채3년" in features and "회사채3년" in indicators:
            corp_series = indicators.get("회사채3년", [])
            gov_series = indicators.get("국고채3년", [])
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

    def get_all_features(self, db_conn=None) -> Dict:
        """Get all macro-economic features."""
        return self.get_macro_from_db(db_conn)
