"""
Naver 종목토론방 Collector
==========================

네이버 금융 종목토론방(https://finance.naver.com/item/board.nhn?code={code})
에서 게시글을 수집하는 수집기.

- ``REQUEST_DELAY = 0.7``: 네이버는 자동화를 차단한다 (2026-08-24 KRX 차단
  사건). 요청 간 최소 0.7초 간격을 강제한다.
- 브라우저 User-Agent + ``Referer: https://finance.naver.com/`` 를 반드시
  전송한다.
- HTTP 403/5xx/타임아웃 시 예외를 던지지 않고 해당 종목을 건너뛴다
  (fail-open).
- HTML 파싱은 stdlib ``html.parser.HTMLParser`` 를 사용한다. 필드가 없으면
  None/기본값을 사용하며 크래시하지 않는다.

이 파일은 DB/네트워크에 의존하지 않으므로 호스트 pytest 에서 그대로 import
가능하다. aiohttp 는 선택 의존성으로, 없으면 네트워크 수집은 빈 리스트를
반환한다.
"""

import asyncio
import logging
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from .sns_interface import SnsPost

logger = logging.getLogger(__name__)

try:
    import aiohttp
except ImportError:
    aiohttp = None


class NaverBoardCollector:
    """네이버 종목토론방 게시글 수집기."""

    NAVER_FINANCE_BASE = "https://finance.naver.com"
    #: 요청 간 최소 간격(초). 네이버 자동화 차단 대응 (2026-08-24 KRX 사건).
    REQUEST_DELAY = 0.7
    MAX_POSTS_PER_STOCK = 20

    def __init__(self):
        self._last_request = 0.0

    async def collect_all(self, stock_codes: Optional[List[str]] = None) -> List[SnsPost]:
        """지정된 종목들의 종목토론방 게시글을 수집한다.

        Args:
            stock_codes: 수집할 종목 코드 목록. None 이면 기본 종목
                (["005930"])을 사용한다.

        Returns:
            수집된 ``SnsPost`` 목록. 개별 종목 실패 시 해당 종목은 건너뛰고
            부분 결과를 반환한다. 예외를 던지지 않는다.
        """
        if stock_codes is None:
            stock_codes = self._get_default_stock_codes()

        posts: List[SnsPost] = []
        for code in stock_codes:
            try:
                stock_posts = await self._fetch_board(code)
                posts.extend(stock_posts)
            except Exception as e:
                # 종목 단위 실패는 로그만 남기고 계속 진행 (fail-open)
                logger.debug(f"Naver board collection failed for {code}: {e}")
                continue

        logger.info(f"Collected {len(posts)} Naver board posts")
        return posts

    async def _fetch_board(self, stock_code: str) -> List[SnsPost]:
        """단일 종목의 종목토론방 HTML 을 가져와 파싱한다."""
        if not aiohttp:
            logger.debug("aiohttp not installed; Naver board collection skipped")
            return []

        url = f"{self.NAVER_FINANCE_BASE}/item/board.nhn?code={stock_code}"

        await self._rate_limit()

        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://finance.naver.com/",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ko-KR,ko;q=0.9",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        return self.parse_board_html(html, stock_code)
                    elif resp.status == 403:
                        logger.debug(f"Access denied (403) for stock {stock_code}")
                    else:
                        logger.debug(f"HTTP {resp.status} for stock {stock_code}")
        except asyncio.TimeoutError:
            logger.debug(f"Timeout fetching board for {stock_code}")
        except Exception as e:
            logger.debug(f"Board fetch error for {stock_code}: {e}")

        return []

    def parse_board_html(self, html: str, stock_code: str) -> List[SnsPost]:
        """종목토론방 HTML 을 파싱해 ``SnsPost`` 목록을 반환한다.

        게시글은 ``<tr>`` 행 단위로 존재하며, 각 행에는 제목 링크
        (``<a title="...">``), 작성자, 날짜, 본문 등이 포함된다. 필드가
        누락되면 None/기본값을 사용하고 크래시하지 않는다.

        Args:
            html: 종목토론방 페이지 HTML 문자열.
            stock_code: 해당 종목 코드.

        Returns:
            파싱된 ``SnsPost`` 목록.
        """
        posts: List[SnsPost] = []
        try:
            parser = _BoardParser()
            parser.feed(html)
            for row in parser.rows[: self.MAX_POSTS_PER_STOCK]:
                post = self._row_to_post(row, stock_code)
                if post is not None:
                    posts.append(post)
        except Exception as e:
            logger.debug(f"Naver board HTML parsing error: {e}")

        return posts

    def _row_to_post(self, row: dict, stock_code: str) -> Optional[SnsPost]:
        """파싱된 행 dict 를 ``SnsPost`` 로 변환한다.

        Args:
            row: ``_BoardParser`` 가 추출한 행 데이터.
            stock_code: 종목 코드.

        Returns:
            변환된 ``SnsPost``. post_id 가 없으면 None.
        """
        post_id = row.get("post_id")
        if not post_id:
            return None

        # 날짜 파싱 (여러 형식 방어)
        posted_at = None
        raw_date = row.get("date")
        if raw_date:
            posted_at = self._parse_date(raw_date)

        return SnsPost(
            source="naver_board",
            post_id=post_id,
            stock_code=stock_code,
            author_id=row.get("author_id"),
            author_name=row.get("author_name"),
            posted_at=posted_at,
            text=row.get("text") or row.get("title"),
            raw_json=row,
        )

    @staticmethod
    def _parse_date(raw_date: str) -> Optional[datetime]:
        """여러 날짜 형식을 방어적으로 파싱한다.

        네이버 종목토론방은 날짜를 ``2026.08.24 10:30`` 또는
        ``2026-08-24 10:30:00`` 형태로 제공한다. 파싱 실패 시 None.
        """
        raw = raw_date.strip()
        for fmt in ("%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    async def _rate_limit(self):
        """요청 간 최소 간격(REQUEST_DELAY)을 강제한다."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request
        if elapsed < self.REQUEST_DELAY:
            await asyncio.sleep(self.REQUEST_DELAY - elapsed)
        self._last_request = asyncio.get_event_loop().time()

    @staticmethod
    def _get_default_stock_codes() -> List[str]:
        """기본 종목 코드 목록을 반환한다."""
        return ["005930"]


class _BoardParser(HTMLParser):
    """종목토론방 HTML 을 행 단위로 파싱하는 내부 파서.

    ``<tr>`` 행을 추적하며, 각 행에서 다음을 추출한다:
      - post_id: 게시글 링크 href 의 ``no`` 파라미터
      - title: ``<a title="...">`` 의 title 속성
      - author_id / author_name: 작성자 링크
      - date: 날짜 셀 텍스트
      - text: 본문 셀 텍스트
    """

    def __init__(self):
        super().__init__()
        self.rows: List[dict] = []
        self._in_tr = False
        self._current_row: Optional[dict] = None
        self._in_td = False
        self._current_td_class = ""
        self._td_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._in_tr = True
            self._current_row = {}
        elif tag == "td" and self._in_tr:
            self._in_td = True
            self._current_td_class = attrs.get("class", "")
            self._td_text = []
        elif tag == "a" and self._in_tr:
            self._handle_link(attrs)

    def handle_endtag(self, tag):
        if tag == "td" and self._in_td:
            self._in_td = False
            self._flush_td()
        elif tag == "tr" and self._in_tr:
            self._in_tr = False
            if self._current_row is not None:
                self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data):
        if self._in_td:
            self._td_text.append(data)

    def _handle_link(self, attrs: dict):
        """행 내 링크에서 post_id / title / author 를 추출한다."""
        if self._current_row is None:
            return

        href = attrs.get("href", "")
        title = attrs.get("title", "")

        # 게시글 링크: href 에 no= 파라미터가 있으면 post_id 로 사용
        if "no=" in href:
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            if "no" in params:
                self._current_row["post_id"] = params["no"][0]
            if title:
                self._current_row["title"] = title.strip()

        # 작성자 링크: href 에 user_id 파라미터가 있으면 author_id 로 사용
        if "user_id" in href or "userId" in href:
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            for key in ("user_id", "userId"):
                if key in params:
                    self._current_row["author_id"] = params[key][0]
                    break
            if title:
                self._current_row["author_name"] = title.strip()

    def _flush_td(self):
        """현재 td 의 텍스트를 행 데이터로 저장한다."""
        if self._current_row is None:
            return
        text = "".join(self._td_text).strip()
        cls = self._current_td_class

        if "date" in cls:
            self._current_row["date"] = text
        elif "author" in cls or "pname" in cls:
            self._current_row["author_name"] = text
        elif "title" in cls:
            self._current_row["text"] = text
        elif text and "text" not in self._current_row:
            # 본문 셀 (class 미지정) — 첫 번째 비어있지 않은 텍스트를 본문으로
            self._current_row["text"] = text
