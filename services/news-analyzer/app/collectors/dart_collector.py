"""
DART Disclosure Collector
Collects corporate disclosures from DART OpenAPI.
"""

import os
import logging
from typing import List, Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode

from app.models.schemas import Article

logger = logging.getLogger(__name__)

try:
    import aiohttp
except ImportError:
    aiohttp = None


class DartCollector:
    """Collects recent disclosures from DART OpenAPI."""

    DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("DART_API_KEY", "")

    async def collect_all(self) -> List[Article]:
        """Collect recent disclosures for tracked stocks."""
        if not self.api_key or self.api_key.startswith("your_"):
            logger.warning("DART_API_KEY not configured; skipping disclosure collection")
            return []

        stocks = self._get_tracked_stocks()
        articles = []

        for code in stocks:
            try:
                stock_articles = await self._fetch_disclosures(code)
                articles.extend(stock_articles)
            except Exception as e:
                logger.debug(f"DART collection failed for {code}: {e}")
                continue

        logger.info(f"Collected {len(articles)} disclosures from DART")
        return articles

    async def _fetch_disclosures(self, stock_code: str) -> List[Article]:
        """Fetch recent disclosures for a single stock."""
        if not aiohttp:
            return []

        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)

        params = {
            "crtfc_key": self.api_key,
            "corp_code": stock_code,
            "bgn_de": start_date.strftime("%Y%m%d"),
            "end_de": end_date.strftime("%Y%m%d"),
            "page_no": "1",
            "page_count": "10",
            "sort": "date",
            "sort_mth": "desc",
        }

        url = f"{self.DART_LIST_URL}?{urlencode(params)}"
        articles = []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()

            if data.get("status") != "000":
                return []

            for item in data.get("list", []):
                title = item.get("report_nm", "")
                if not title:
                    continue

                rcept_dt = item.get("rcept_dt", "")
                pub_date = None
                if rcept_dt and len(rcept_dt) == 8:
                    pub_date = datetime(
                        int(rcept_dt[:4]), int(rcept_dt[4:6]), int(rcept_dt[6:8])
                    )

                articles.append(Article(
                    source="DART",
                    title=title,
                    content=title[:1000],
                    url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no', '')}",
                    published_at=pub_date or datetime.now(),
                ))

        except Exception as e:
            logger.debug(f"DART fetch error for {stock_code}: {e}")

        return articles

    def _get_tracked_stocks(self) -> List[str]:
        """Get list of tracked stock codes. Override with actual DB query."""
        return ["005930"]
