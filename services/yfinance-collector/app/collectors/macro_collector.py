"""
Macro Collector
Collects macro-economic indicators from Bank of Korea ECOS API.

ECOS API docs: https://ecos.bok.or.kr
Requires: ECOS_API_KEY in environment
"""

import os
import logging
from typing import Dict, List, Optional
from datetime import datetime
import json

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None


class MacroCollector:
    """Collects macro-economic indicators from Bank of Korea ECOS API."""

    ECOS_BASE_URL = "https://ecos.bok.or.kr/api"

    INDICATOR_CODES = {
        "기준금리": ("722Y001", "D", "0101000"),
        "국고채3년": ("721Y001", "D", "0102000"),
        "회사채3년": ("721Y001", "D", "0103000"),
        "USD/KRW 환율": ("731Y001", "D", "0000001"),
        "JPY/KRW 환율": ("731Y001", "D", "0000002"),
        "CNY/KRW 환율": ("731Y001", "D", "0000005"),
        "WTI 유가": ("732Y001", "D", "0000001"),
        "CPI": ("901Y009", "M", "0"),
        "PPI": ("901Y010", "M", "0"),
    }

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("ECOS_API_KEY", "")
        self.session = requests.Session() if requests else None

    def _request(
        self, stat_code: str, cycle: str, start_date: str,
        end_date: str, item_code: str = "?"
    ) -> Optional[Dict]:
        """Make an ECOS API request."""
        if not self.session:
            logger.warning("requests library not available")
            return None
        if not self.api_key or self.api_key.startswith("your_"):
            logger.warning("ECOS_API_KEY not configured")
            return None

        url = (
            f"{self.ECOS_BASE_URL}/{self.api_key}/json/kr/"
            f"{stat_code}/{cycle}/"
            f"{start_date}/{end_date}/{item_code}"
        )

        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"ECOS API request failed: {e}")
            return None

    def collect_macro_indicators(self) -> List[Dict]:
        """
        Collect all macro-economic indicators.

        Returns:
            List of dicts with indicator_name, date, value, unit
        """
        results = []
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = f"{datetime.now().year - 1}0101"

        for name, (stat_code, cycle, item_code) in self.INDICATOR_CODES.items():
            try:
                data = self._request(stat_code, cycle, start_date, end_date, item_code)
                if not data or "StatisticSearch" not in data:
                    logger.debug(f"No ECOS data for {name}")
                    continue

                rows = data["StatisticSearch"].get("row", [])
                for row in rows:
                    time_str = row.get("TIME", "")
                    data_value = row.get("DATA_VALUE", "")

                    if not time_str or not data_value:
                        continue

                    try:
                        value = float(data_value.replace(",", ""))
                    except (ValueError, AttributeError):
                        continue

                    date_obj = self._parse_date(time_str, cycle)
                    if date_obj:
                        results.append({
                            "indicator_name": name,
                            "date": date_obj.strftime("%Y-%m-%d"),
                            "value": value,
                            "unit": self._get_unit(name),
                        })

            except Exception as e:
                logger.debug(f"Failed to collect {name}: {e}")
                continue

        return results

    def _parse_date(self, time_str: str, cycle: str) -> Optional[datetime]:
        """Parse ECOS time string to date."""
        if cycle == "D":
            try:
                return datetime.strptime(time_str, "%Y%m%d")
            except ValueError:
                return None
        elif cycle == "M":
            try:
                return datetime.strptime(time_str, "%Y%m") + __import__("datetime").timedelta(weeks=4)
            except ValueError:
                return None
        return None

    def _get_unit(self, indicator_name: str) -> str:
        """Get unit for an indicator."""
        units = {
            "기준금리": "percent",
            "국고채3년": "percent",
            "회사채3년": "percent",
            "USD/KRW 환율": "KRW",
            "JPY/KRW 환율": "KRW",
            "CNY/KRW 환율": "KRW",
            "WTI 유가": "USD/barrel",
            "CPI": "index",
            "PPI": "index",
        }
        return units.get(indicator_name, "")
