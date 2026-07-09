"""
Financial Collector
Collects financial statement data from DART OpenAPI (금융감독원 전자공시).

DART API docs: https://opendart.fss.or.kr
Requires: DART_API_KEY in environment
"""

import os
import logging
from typing import Dict, Optional, List
from datetime import datetime
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None


class FinancialCollector:
    """Collects financial statements from DART OpenAPI."""

    DART_BASE_URL = "https://opendart.fss.or.kr/api"

    FINANCIAL_METRICS_MAP = {
        "ifrs-full_Revenue": "revenue",
        "ifrs-full_ProfitLossFromOperatingActivities": "operating_profit",
        "ifrs-full_ProfitLoss": "net_income",
        "ifrs-full_Assets": "total_assets",
        "ifrs-full_Equity": "total_equity",
        "ifrs-full_DebtSecurities": "total_debt",
    }

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("DART_API_KEY", "")
        self.session = requests.Session() if requests else None

    def _request(self, endpoint: str, params: Dict) -> Optional[Dict]:
        """Make a DART API request."""
        if not self.session:
            logger.warning("requests library not available")
            return None
        if not self.api_key or self.api_key.startswith("your_"):
            logger.warning("DART_API_KEY not configured")
            return None

        params["crtfc_key"] = self.api_key
        try:
            resp = self.session.get(
                f"{self.DART_BASE_URL}/{endpoint}",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if "xml" in content_type:
                return self._parse_xml(resp.text)
            return resp.json()
        except Exception as e:
            logger.error(f"DART API request failed ({endpoint}): {e}")
            return None

    def _parse_xml(self, xml_text: str) -> Dict:
        """Parse DART XML response."""
        try:
            root = ET.fromstring(xml_text)
            result = {}
            for child in root:
                result[child.tag] = child.text or ""
            return result
        except Exception:
            return {}

    def collect_financials(
        self, stock_code: str, year: str = None
    ) -> List[Dict]:
        """
        Collect financial statement data for a stock.

        Args:
            stock_code: Stock code (e.g. '005930')
            year: Fiscal year (e.g. '2024'), defaults to current year

        Returns:
            List of dicts with financial statement rows
        """
        if year is None:
            year = str(datetime.now().year)

        results = []

        corp_code = self._get_corp_code(stock_code)
        if not corp_code:
            return results

        for reprt_code in ["11013", "11012", "11014"]:  # 1Q, 2Q, 3Q
            data = self._request("fnlttSinglAcntAll.json", {
                "corp_code": corp_code,
                "bsns_year": year,
                "reprt_code": reprt_code,
                "fs_div": "CFS",
            })
            if data and data.get("status") == "000":
                for item in data.get("list", []):
                    metric_name = item.get("account_nm", "")
                    short_name = self.FINANCIAL_METRICS_MAP.get(metric_name)
                    if short_name:
                        results.append({
                            "stock_code": stock_code,
                            "report_date": f"{year}-{reprt_code[:2]}-{reprt_code[2:]}",
                            "metric": short_name,
                            "value": float(item.get("thstrm_amount", "0").replace(",", "") or 0),
                            "unit": "KRW",
                        })
            else:
                logger.debug(f"No DART data for {stock_code} {year} {reprt_code}")

        # Annual report
        data = self._request("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": year,
            "reprt_code": "11011",
            "fs_div": "CFS",
        })
        if data and data.get("status") == "000":
            for item in data.get("list", []):
                metric_name = item.get("account_nm", "")
                short_name = self.FINANCIAL_METRICS_MAP.get(metric_name)
                if short_name:
                    results.append({
                        "stock_code": stock_code,
                        "report_date": f"{year}-12-31",
                        "metric": short_name,
                        "value": float(item.get("thstrm_amount", "0").replace(",", "") or 0),
                        "unit": "KRW",
                    })

        return results

    def _get_corp_code(self, stock_code: str) -> Optional[str]:
        """Get DART corporate code from stock code."""
        data = self._request("company.json", {"corp_code": stock_code})
        if not data:
            return None
        if data.get("status") == "000":
            return data.get("corp_code")
        return None

    def aggregate_to_financials(self, raw_data: List[Dict]) -> Dict:
        """Aggregate raw DART data into a single financial statement dict."""
        if not raw_data:
            return {}

        latest = {}
        for item in raw_data:
            date_key = item["report_date"]
            if date_key not in latest:
                latest[date_key] = {}
            latest[date_key][item["metric"]] = item["value"]

        sorted_dates = sorted(latest.keys())
        result = latest[sorted_dates[-1]] if sorted_dates else {}

        result["stock_code"] = raw_data[0]["stock_code"]
        result["report_date"] = sorted_dates[-1] if sorted_dates else None

        return result
