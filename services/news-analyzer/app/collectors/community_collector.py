"""
Community Collector
Collects posts from Naver stock discussion boards.
"""

import logging
import asyncio
from typing import List, Optional
from datetime import datetime
from urllib.parse import quote

from app.models.schemas import Article

logger = logging.getLogger(__name__)

try:
    import aiohttp
except ImportError:
    aiohttp = None


class CommunityCollector:
    """Collects posts from Naver stock discussion (종목토론실)."""

    NAVER_FINANCE_BASE = "https://finance.naver.com"
    REQUEST_DELAY = 3.0
    MAX_POSTS_PER_STOCK = 10

    def __init__(self):
        self._last_request = 0.0

    async def collect_all(self, stock_codes: List[str] = None) -> List[Article]:
        """Collect discussion posts for tracked stocks."""
        if stock_codes is None:
            stock_codes = self._get_tracked_stocks()

        articles = []
        for code in stock_codes:
            try:
                stock_articles = await self._fetch_discussions(code)
                articles.extend(stock_articles)
            except Exception as e:
                logger.debug(f"Community collection failed for {code}: {e}")
                continue

        logger.info(f"Collected {len(articles)} community posts")
        return articles

    async def _fetch_discussions(self, stock_code: str) -> List[Article]:
        """Fetch recent discussion posts for a stock from Naver."""
        if not aiohttp:
            return []

        articles = []
        url = (
            f"{self.NAVER_FINANCE_BASE}/item/board.naver"
            f"?code={stock_code}"
        )

        await self._rate_limit()

        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ko-KR,ko;q=0.9",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        posts = self._parse_discussion_html(html, stock_code)
                        articles.extend(posts)
                    elif resp.status == 403:
                        logger.debug(f"Access denied for stock {stock_code} discussion")
                    else:
                        logger.debug(f"HTTP {resp.status} for {stock_code}")

        except asyncio.TimeoutError:
            logger.debug(f"Timeout fetching discussions for {stock_code}")
        except Exception as e:
            logger.debug(f"Discussion fetch error for {stock_code}: {e}")

        return articles

    def _parse_discussion_html(self, html: str, stock_code: str) -> List[Article]:
        """Parse Naver discussion board HTML."""
        articles = []
        try:
            from html.parser import HTMLParser

            class DiscussionParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.in_title = False
                    self.titles = []
                    self.current_title = ""

                def handle_starttag(self, tag, attrs):
                    if tag == "a":
                        for attr_name, attr_val in attrs:
                            if attr_name == "title" and attr_val:
                                self.titles.append(attr_val)

            parser = DiscussionParser()
            parser.feed(html)

            for title in parser.titles[:self.MAX_POSTS_PER_STOCK]:
                if title.strip():
                    articles.append(Article(
                        source="naver_cafe",
                        title=title.strip(),
                        content=title.strip(),
                        url=f"{self.NAVER_FINANCE_BASE}/item/board.naver?code={stock_code}",
                        published_at=datetime.now(),
                    ))

        except Exception as e:
            logger.debug(f"HTML parsing error: {e}")

        return articles

    async def _rate_limit(self):
        """Enforce request rate limit."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request
        if elapsed < self.REQUEST_DELAY:
            await asyncio.sleep(self.REQUEST_DELAY - elapsed)
        self._last_request = asyncio.get_event_loop().time()

    def _get_tracked_stocks(self) -> List[str]:
        """Get list of tracked stock codes."""
        return ["005930"]


class SnsCollector:
    """Collects SNS data from configured sources."""

    def __init__(self):
        self._community = CommunityCollector()

    async def collect_all(self) -> List[Article]:
        """Collect from all configured SNS sources."""
        articles = []

        try:
            community_posts = await self._community.collect_all()
            for post in community_posts:
                post.source = "sns"
            articles.extend(community_posts)
        except Exception as e:
            logger.debug(f"SNS collection failed: {e}")

        return articles
