"""
RSS News Collector
Fetches articles from configured RSS news sources.
"""

import feedparser
import logging
from typing import List
from datetime import datetime
from app.models.schemas import Article
from app.normalization.normalizer import normalize_article
from app.normalization.dedup import dedupe

logger = logging.getLogger(__name__)


class RssCollector:
    """Collects news articles from RSS feeds."""

    SOURCES = [
        {
            "name": "한국경제신문",
            "type": "rss",
            # 2026-08: hankyung.com/feed 가 JS 렌더링 HTML로 변경됨 → Google News
            # 사이트검색 RSS로 대체 (entries=0/bozo=1 실측 확인)
            "url": "https://news.google.com/rss/search?q=site:www.hankyung.com&hl=ko&gl=KR&ceid=KR:ko",
        },
        {
            "name": "매일경제",
            "type": "rss",
            "url": "https://www.mk.co.kr/rss/30000001/",
        },
        {
            "name": "서울경제",
            "type": "rss",
            # 2026-08: sedaily.com/Feed/SEH 가 404 → Google News 사이트검색 RSS로 대체
            "url": "https://news.google.com/rss/search?q=site:www.sedaily.com&hl=ko&gl=KR&ceid=KR:ko",
        },
        {
            "name": "이데일리",
            "type": "rss",
            # 2026-08: edaily.co.kr/feed/edaily.xml 가 HTML 페이지로 변경 → Google News RSS 대체
            "url": "https://news.google.com/rss/search?q=site:www.edaily.co.kr&hl=ko&gl=KR&ceid=KR:ko",
        },
        {
            "name": "머니투데이",
            "type": "rss",
            # 2026-08: news.mt.co.kr/rss/mt_recent.xml 가 410 Gone → Google News RSS 대체
            "url": "https://news.google.com/rss/search?q=site:news.mt.co.kr&hl=ko&gl=KR&ceid=KR:ko",
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

        # Normalize each article; discard invalid (empty/short content).
        normalized = []
        for article in articles:
            norm = normalize_article(article)
            if norm is None:
                logger.debug(f"Dropped invalid article: {article.title[:50]}")
                continue
            normalized.append(norm)

        # Content-based dedup (keeps one copy of the same story across media).
        unique, dropped = dedupe(normalized)
        for dup in dropped:
            logger.info(
                f"Dropped duplicate: {dup.title[:50]} (source={dup.source})"
            )
        return unique

    async def _fetch_feed(self, source: dict) -> List[Article]:
        """Fetch and parse a single RSS feed.

        2026-08: User-Agent 헤더 필수 (기본 UA는 일부 사이트/Google News 에서 차단될 수
        있음 — 실측: urllib 기본 UA로 hankyung 200-html, Google News 는 UA 필수).
        """
        import urllib.request

        req = urllib.request.Request(
            source["url"],
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            feed = feedparser.parse(resp.read())
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
