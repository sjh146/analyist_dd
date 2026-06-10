"""
RSS News Collector
Fetches articles from configured RSS news sources.
"""

import feedparser
import logging
from typing import List
from datetime import datetime
from app.models.schemas import Article

logger = logging.getLogger(__name__)


class RssCollector:
    """Collects news articles from RSS feeds."""

    SOURCES = [
        {
            "name": "한국경제신문",
            "type": "rss",
            "url": "https://www.hankyung.com/feed",
        },
        {
            "name": "매일경제",
            "type": "rss",
            "url": "https://www.mk.co.kr/rss/30000001/",
        },
        {
            "name": "서울경제",
            "type": "rss",
            "url": "https://www.sedaily.com/Feed/SEH",
        },
        {
            "name": "이데일리",
            "type": "rss",
            "url": "https://www.edaily.co.kr/feed/edaily.xml",
        },
        {
            "name": "머니투데이",
            "type": "rss",
            "url": "https://news.mt.co.kr/rss/mt_recent.xml",
        },
    ]

    async def collect_all(self) -> List[Article]:
        """Collect articles from all configured RSS sources."""
        articles = []
        for source in self.SOURCES:
            try:
                source_articles = await self._fetch_feed(source)
                articles.extend(source_articles)
                logger.info(
                    f"Collected {len(source_articles)} articles from {source['name']}"
                )
            except Exception as e:
                logger.error(f"Failed to fetch {source['name']}: {e}")
        return articles

    async def _fetch_feed(self, source: dict) -> List[Article]:
        """Fetch and parse a single RSS feed."""
        feed = feedparser.parse(source["url"])
        articles = []

        for entry in feed.entries[:20]:  # Max 20 per source per cycle
            title = entry.get("title", "")
            content = entry.get("summary", entry.get("description", ""))
            link = entry.get("link", "")

            # Parse published date
            published = None
            if "published_parsed" in entry and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6])

            article = Article(
                source=source["name"],
                title=title,
                content=content,
                url=link,
                published_at=published or datetime.now(),
            )
            articles.append(article)

        return articles
