"""
Naver 종목토론방 Collector (2026-09 SPA 전환 대응)
=================================================

네이버 금융이 2026-09 파이낸스 SPA 로 전환하면서 종전 HTML 엔드포인트
``https://finance.naver.com/item/board.nhn?code={code}`` 는 **HTTP 302 →
``https://stock.naver.com/domestic/stock/{code}/discussion``** 로 리다이렉트되어
본문(HTML)을 받을 수 없게 됐다(실측: status 302, body 0바이트). 따라서 수집
경로를 SPA 백엔드 JSON API 로 교체했다.

확정 API (2026-09-24 실측, 브라우저 네트워크 관찰 + curl 확인)
-------------------------------------------------------------
1) 게시글 목록 (커서 페이징)::

   GET https://stock.naver.com/api/community/discussion/posts/by-item
       ?discussionType=domesticStock
       &itemCode={code}
       &isHolderOnly=false
       &excludesItemNews=false
       &isItemNewsOnly=false      (true 로 두면 뉴스만 오고 토론글이 빈다)
       &isCleanbotPassedOnly=false
       &pageSize={n}              (최대 100)
       &offset={cursor}           (생략 = 최신. 응답 ``lastOffset`` 을 다음
                                   호출의 ``offset`` 으로 넘겨 과거로 이동.
                                   offset 은 전역 게시글 id 의 음수값이라
                                   임의 과거 시점으로 점프도 가능하다)

   응답::

       {"offset": "-9223372036854775807", "pageSize": 100,
        "posts": [ {"id": "429804991", "itemCode": "005930",
                    "itemName": "삼성전자", "postType": "normal",
                    "writer": {"profileId": "28660109605862322",
                               "nickname": "..."},
                    "writtenAt": "2026-09-24T01:50:56",
                    "title": "...", "contentSwReplacedButImg": "...",
                    "commentCount": 0, "recommendCount": 0,
                    "viewCount": 0, "isCleanbotPassed": true, ...} ],
        "lastOffset": "-429804153"}

   주의: 응답 문자열에 **잘못된 JSON 이스케이프**(예: ``\\d``)와 **원시 제어문자**가
   섞여 있어 ``json.loads`` 기본 파서가 실패한다 → ``_loads_json`` 참조.

2) 공감/비공감/조회수 (목록 응답의 recommendCount 는 항상 0)::

   GET .../posts/reactions?postIds=429804991,429804928
   → [{"postId":"429804991","recommendCount":1,"notRecommendCount":2,
       "viewCount":2}, ...]

3) 댓글 수 (목록 응답의 commentCount 는 항상 0)::

   GET .../posts/comment-counts?postIds=429804991,429804928
   → {"commentCounts":[{"postId":"429804991","commentCount":0}, ...]}

설계 규칙
---------
- ``REQUEST_DELAY = 0.7``: 종전과 동일한 최소 요청 간격(초). 네이버 자동화
  차단 대응(2026-08-24 KRX 사건). 러너는 이보다 느리게 돌아도 무방하다.
- 브라우저 User-Agent + ``Referer: https://stock.naver.com/`` 를 전송한다.
- HTTP 오류/타임아웃/파싱 실패 시 예외를 던지지 않고 해당 종목을 건너뛴다
  (fail-open) — 종목 단위 실패가 전체 수집을 막지 않는다.
- 레거시 HTML 파서(``parse_board_html``/``_BoardParser``)는 **후방 호환용**으로
  남겨 둔다(기존 단위테스트가 그대로 통과해야 한다). 네트워크 경로는 더 이상
  HTML 을 쓰지 않는다.

이 파일은 DB/네트워크에 의존하지 않으므로 호스트 pytest 에서 그대로 import
가능하다. aiohttp 는 선택 의존성으로, 없으면 네트워크 수집은 빈 리스트를
반환한다.
"""

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .sns_interface import SnsPost

logger = logging.getLogger(__name__)

try:
    import aiohttp
except ImportError:
    aiohttp = None

#: KST (UTC+9) — 네이버 ``writtenAt`` 은 로컬(KST) 시각으로 온다.
KST = timezone(timedelta(hours=9))

#: 잘못된 JSON 이스케이프(예: ``\d``)를 이중 백슬래시로 되돌리는 정규식.
#: 유효 이스케이프(``\" \\ \/ \b \f \n \r \t \uXXXX``)는 건드리지 않는다.
_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')

#: 네이버 게시글 id 커서의 최신(=최대) 값. 첫 페이지 요청에 쓰인다.
LATEST_CURSOR = "-9223372036854775807"


class NaverBoardCollector:
    """네이버 종목토론방 게시글 수집기 (stock.naver.com JSON API 기반)."""

    #: 레거시 HTML 경로 (2026-09 SPA 전환으로 302 → 사용하지 않음).
    NAVER_FINANCE_BASE = "https://finance.naver.com"
    #: 현행 SPA 백엔드.
    API_BASE = "https://stock.naver.com/api/community/discussion"
    POSTS_PATH = "/posts/by-item"
    REACTIONS_PATH = "/posts/reactions"
    COMMENT_COUNTS_PATH = "/posts/comment-counts"

    #: 요청 간 최소 간격(초). 네이버 자동화 차단 대응 (2026-08-24 KRX 사건).
    REQUEST_DELAY = 0.7
    #: 레거시 기본값 (HTML 파서용). API 경로는 ``PAGE_SIZE`` 를 쓴다.
    MAX_POSTS_PER_STOCK = 20

    #: API 페이지 크기 (네이버 상한 100).
    PAGE_SIZE = 100
    #: 종목당 최대 페이지 수 (과도한 요청 금지).
    MAX_PAGES_PER_STOCK = 5
    #: 수집 윈도우(일). 이보다 오래된 글을 만나면 해당 종목 순회를 멈춘다.
    LOOKBACK_DAYS = 15
    #: postIds 배치 크기 (reactions/comment-counts 조회).
    ENRICH_CHUNK = 100

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self._last_request = 0.0

    # ════════════════════════════════════════════════════════════════════
    # 공개 API
    # ════════════════════════════════════════════════════════════════════
    async def collect_all(
        self,
        stock_codes: Optional[List[str]] = None,
        lookback_days: Optional[int] = None,
        max_pages: Optional[int] = None,
        page_size: Optional[int] = None,
        enrich: bool = True,
    ) -> List[SnsPost]:
        """지정된 종목들의 종목토론방 게시글을 수집한다.

        Args:
            stock_codes: 수집할 종목 코드 목록. None 이면 기본 종목
                (``["005930"]``)을 사용한다.
            lookback_days: 수집 윈도우(일). None 이면 ``LOOKBACK_DAYS``.
            max_pages: 종목당 최대 페이지 수. None 이면 ``MAX_PAGES_PER_STOCK``.
            page_size: 페이지 크기(최대 100). None 이면 ``PAGE_SIZE``.
            enrich: True 면 공감/댓글 수를 별도 엔드포인트로 보강한다.

        Returns:
            수집된 ``SnsPost`` 목록. 개별 종목 실패 시 해당 종목은 건너뛰고
            부분 결과를 반환한다. 예외를 던지지 않는다.
        """
        if stock_codes is None:
            stock_codes = self._get_default_stock_codes()

        posts: List[SnsPost] = []
        for code in stock_codes:
            try:
                stock_posts = await self._fetch_board(
                    code,
                    lookback_days=lookback_days,
                    max_pages=max_pages,
                    page_size=page_size,
                    enrich=enrich,
                )
                posts.extend(stock_posts)
            except Exception as e:
                # 종목 단위 실패는 로그만 남기고 계속 진행 (fail-open)
                logger.debug(f"Naver board collection failed for {code}: {e}")
                continue

        logger.info(f"Collected {len(posts)} Naver board posts")
        return posts

    def api_posts_url(self, stock_code: str, offset: Optional[str] = None,
                      page_size: Optional[int] = None) -> str:
        """게시글 목록 API URL 을 만든다 (테스트/디버깅용으로 분리)."""
        size = int(page_size or self.PAGE_SIZE)
        url = (
            f"{self.API_BASE}{self.POSTS_PATH}"
            f"?discussionType=domesticStock&itemCode={stock_code}"
            f"&isHolderOnly=false&excludesItemNews=false"
            f"&isItemNewsOnly=false&isCleanbotPassedOnly=false"
            f"&pageSize={size}"
        )
        if offset:
            url += f"&offset={offset}"
        return url

    # ════════════════════════════════════════════════════════════════════
    # JSON 방어 파싱
    # ════════════════════════════════════════════════════════════════════
    @staticmethod
    def _fix_json_escapes(text: str) -> str:
        """잘못된 JSON 이스케이프를 이중 백슬래시로 바꿔 유효하게 만든다.

        네이버 응답에는 게시글 본문(사용자 입력)에서 온 ``\\d`` 같은 잘못된
        이스케이프가 섞인다 → 표준 파서가 실패한다.
        """
        return _BAD_ESCAPE.sub(r"\\\\", text)

    @classmethod
    def _loads_json(cls, text: str):
        """관대한 JSON 파서 (잘못된 이스케이프 + 원시 제어문자 허용)."""
        try:
            return json.loads(text, strict=False)
        except ValueError:
            return json.loads(cls._fix_json_escapes(text), strict=False)

    # ════════════════════════════════════════════════════════════════════
    # 응답 → SnsPost
    # ════════════════════════════════════════════════════════════════════
    @staticmethod
    def _parse_written_at(raw: Optional[str]) -> Optional[datetime]:
        """``2026-09-24T01:50:56`` (KST, naive) → datetime. 실패 시 None."""
        if not raw:
            return None
        raw = str(raw).strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                    "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    def parse_api_post(self, item: Dict, stock_code: Optional[str] = None) -> Optional[SnsPost]:
        """API 게시글 dict 1건을 ``SnsPost`` 로 변환한다 (id 없으면 None)."""
        if not isinstance(item, dict):
            return None
        post_id = item.get("id")
        if post_id is None:
            return None
        writer = item.get("writer") or {}
        title = (item.get("title") or "").strip()
        body = (item.get("contentSwReplacedButImg")
                or item.get("contentSwReplaced") or "").strip()
        text = title
        if body and body != title:
            text = f"{title}\n{body}" if title else body

        return SnsPost(
            source="naver_board",
            post_id=str(post_id),
            stock_code=item.get("itemCode") or stock_code,
            author_id=(str(writer.get("profileId")) if writer.get("profileId") else None),
            author_name=writer.get("nickname") or None,
            author_followers=0,  # 네이버 종토방은 팔로워 수를 노출하지 않는다.
            posted_at=self._parse_written_at(item.get("writtenAt")),
            text=text or None,
            comment_count=int(item.get("commentCount") or 0),
            like_count=int(item.get("recommendCount") or 0),
            # notRecommendCount 는 '비공감'이므로 retweet_count 로 쓰지 않는다.
            retweet_count=0,
            raw_json={
                "id": str(post_id),
                "itemCode": item.get("itemCode"),
                "itemName": item.get("itemName"),
                "postType": item.get("postType"),
                "writtenAt": item.get("writtenAt"),
                "title": title,
                "viewCount": item.get("viewCount"),
                "notRecommendCount": item.get("notRecommendCount"),
                "isCleanbotPassed": item.get("isCleanbotPassed"),
                "isHolderVerified": item.get("isHolderVerified"),
                "commentCount": item.get("commentCount"),
                "recommendCount": item.get("recommendCount"),
            },
        )

    def parse_api_posts(self, payload, stock_code: Optional[str] = None) -> List[SnsPost]:
        """게시글 목록 응답(dict 또는 JSON 문자열) → ``SnsPost`` 목록."""
        if payload is None:
            return []
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", "replace")
        if isinstance(payload, str):
            try:
                payload = self._loads_json(payload)
            except Exception as e:
                logger.debug(f"Naver API JSON parse failed: {e}")
                return []
        if not isinstance(payload, dict):
            return []
        posts: List[SnsPost] = []
        for item in payload.get("posts") or []:
            post = self.parse_api_post(item, stock_code)
            if post is not None:
                posts.append(post)
        return posts

    def apply_enrichment(self, posts: List[SnsPost], reactions, comment_counts) -> int:
        """reactions / comment-counts 응답을 ``SnsPost`` 에 반영한다.

        Args:
            posts: 보강 대상.
            reactions: ``[{"postId","recommendCount","viewCount",
                "notRecommendCount"}, ...]`` 또는 ``{postId: {...}}``.
            comment_counts: ``{"commentCounts":[{"postId","commentCount"}]}``
                또는 ``{postId: n}``.

        Returns:
            보강된 게시글 수.
        """
        rmap: Dict[str, Dict] = {}
        if isinstance(reactions, dict):
            rmap = {str(k): v for k, v in reactions.items()}
        elif isinstance(reactions, list):
            for r in reactions:
                if isinstance(r, dict) and r.get("postId") is not None:
                    rmap[str(r["postId"])] = r
        elif isinstance(reactions, dict):
            rmap = {}

        cmap: Dict[str, int] = {}
        if isinstance(comment_counts, dict):
            for c in comment_counts.get("commentCounts") or []:
                if isinstance(c, dict) and c.get("postId") is not None:
                    cmap[str(c["postId"])] = int(c.get("commentCount") or 0)
        elif isinstance(comment_counts, list):
            for c in comment_counts:
                if isinstance(c, dict) and c.get("postId") is not None:
                    cmap[str(c["postId"])] = int(c.get("commentCount") or 0)

        enriched = 0
        for p in posts:
            r = rmap.get(p.post_id)
            c = cmap.get(p.post_id)
            if r is None and c is None:
                continue
            if r is not None:
                p.like_count = int(r.get("recommendCount") or 0)
                if isinstance(p.raw_json, dict):
                    p.raw_json["recommendCount"] = p.like_count
                    p.raw_json["notRecommendCount"] = r.get("notRecommendCount")
                    p.raw_json["viewCount"] = r.get("viewCount")
            if c is not None:
                p.comment_count = c
                if isinstance(p.raw_json, dict):
                    p.raw_json["commentCount"] = c
            enriched += 1
        return enriched

    # ════════════════════════════════════════════════════════════════════
    # 네트워크
    # ════════════════════════════════════════════════════════════════════
    async def _get_json(self, session, url: str):
        """GET → 관대한 JSON 파싱. 실패 시 None (fail-open)."""
        await self._rate_limit()
        headers = {
            "User-Agent": self.USER_AGENT,
            "Referer": "https://stock.naver.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return self._loads_json(text)
                if resp.status == 403:
                    logger.debug("Access denied (403): %s", url[:120])
                elif resp.status == 429:
                    logger.debug("Rate limited (429): %s", url[:120])
                else:
                    logger.debug("HTTP %s: %s", resp.status, url[:120])
        except asyncio.TimeoutError:
            logger.debug("Timeout: %s", url[:120])
        except Exception as e:
            logger.debug("fetch error (%s): %s", url[:120], e)
        return None

    async def _fetch_board(
        self,
        stock_code: str,
        lookback_days: Optional[int] = None,
        max_pages: Optional[int] = None,
        page_size: Optional[int] = None,
        enrich: bool = True,
    ) -> List[SnsPost]:
        """단일 종목의 종토방을 커서 페이징으로 ``lookback_days`` 만큼 수집한다."""
        if not aiohttp:
            logger.debug("aiohttp not installed; Naver board collection skipped")
            return []

        window = int(lookback_days or self.LOOKBACK_DAYS)
        pages = int(max_pages or self.MAX_PAGES_PER_STOCK)
        size = min(int(page_size or self.PAGE_SIZE), 100)
        # KST 기준 오늘 - window 일 (날짜 비교용).
        window_start = (datetime.now(KST).date() - timedelta(days=window))

        collected: List[SnsPost] = []
        seen: set = set()
        offset: Optional[str] = None
        pages_done = 0

        async with aiohttp.ClientSession() as session:
            while pages_done < pages:
                payload = await self._get_json(
                    session, self.api_posts_url(stock_code, offset, size)
                )
                pages_done += 1
                if not isinstance(payload, dict):
                    break
                page_posts = self.parse_api_posts(payload, stock_code)
                if not page_posts:
                    break
                new_posts = [p for p in page_posts if p.post_id not in seen]
                for p in new_posts:
                    seen.add(p.post_id)
                collected.extend(new_posts)

                oldest = self._oldest_date(page_posts)
                if oldest is not None and oldest <= window_start:
                    break  # 윈도우 확보 완료.
                if not new_posts:
                    break  # 중복만 반복 → 더 볼 필요 없음.

                next_offset = payload.get("lastOffset")
                if not next_offset or str(next_offset) == str(offset):
                    break
                offset = str(next_offset)

            if enrich and collected:
                try:
                    await self._enrich(session, collected)
                except Exception as e:
                    logger.debug("enrichment failed for %s: %s", stock_code, e)

        logger.debug("stock %s: %d posts (%d pages)", stock_code, len(collected), pages_done)
        return collected

    async def _enrich(self, session, posts: List[SnsPost]) -> int:
        """공감/댓글 수를 배치 조회해 반영한다."""
        total = 0
        ids = [p.post_id for p in posts]
        for i in range(0, len(ids), self.ENRICH_CHUNK):
            chunk = ids[i:i + self.ENRICH_CHUNK]
            joined = ",".join(chunk)
            reactions = await self._get_json(
                session, f"{self.API_BASE}{self.REACTIONS_PATH}?postIds={joined}"
            )
            counts = await self._get_json(
                session, f"{self.API_BASE}{self.COMMENT_COUNTS_PATH}?postIds={joined}"
            )
            total += self.apply_enrichment(posts, reactions, counts)
        return total

    @staticmethod
    def _oldest_date(posts: List[SnsPost]) -> Optional[date]:
        dates = [p.posted_at.date() for p in posts if p.posted_at]
        return min(dates) if dates else None

    # ════════════════════════════════════════════════════════════════════
    # 레거시 HTML 파서 (후방 호환 — 네트워크 경로에서는 더 이상 쓰지 않는다)
    # ════════════════════════════════════════════════════════════════════
    def parse_board_html(self, html: str, stock_code: str) -> List[SnsPost]:
        """(레거시) 종목토론방 HTML 을 파싱해 ``SnsPost`` 목록을 반환한다.

        2026-09 SPA 전환 전 HTML 구조 기준. 신규 경로는 ``parse_api_posts``.
        """
        posts: List[SnsPost] = []
        if not html:
            return posts
        try:
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
            for row in rows[: self.MAX_POSTS_PER_STOCK * 4]:
                # 네이버는 게시글 링크를 board_read.naver / board.naver 로,
                # 게시글 번호 파라미터를 nid= / no= 로 쓴다 (둘 다 허용).
                m = re.search(
                    r"board(?:_read)?\.naver\?code=\d+&(?:nid|no)=(\d+)", row
                )
                if not m:
                    continue
                post_id = m.group(1)
                tm = re.search(r"<a[^>]*board(?:_read)?[^>]*>(.*?)</a>", row, re.S)
                title = re.sub(r"<[^>]+>", "", tm.group(1)).strip() if tm else ""
                dm = re.search(
                    r'<(?:span|td)[^>]*class="[^"]*(?:tah|date)[^"]*"[^>]*>'
                    r'\s*([\d.\-]+ [\d:]+)',
                    row,
                )
                raw_date = dm.group(1) if dm else None
                am = re.search(
                    r'class="[^"]*(?:p11[^"]*|pname|author)[^"]*"[^>]*>(.*?)</td>',
                    row,
                    re.S,
                )
                author = re.sub(r"<[^>]+>", "", am.group(1)).strip() if am else None
                # (레거시 후방 호환) 작성자 링크의 userId= 파라미터를 author_id 로.
                um = re.search(r'<a[^>]*[?&](?:userId|user_id)=([^&"\']+)', row)
                post = SnsPost(
                    source="naver_board",
                    post_id=post_id,
                    stock_code=stock_code,
                    author_id=um.group(1) if um else None,
                    author_name=author or None,
                    posted_at=self._parse_date(raw_date) if raw_date else None,
                    text=title or None,
                    raw_json={"title": title, "date": raw_date, "author": author},
                )
                posts.append(post)
                if len(posts) >= self.MAX_POSTS_PER_STOCK:
                    break
        except Exception as e:
            logger.debug(f"Naver board HTML parsing error: {e}")

        return posts

    def _row_to_post(self, row: dict, stock_code: str) -> Optional[SnsPost]:
        """파싱된 행 dict 를 ``SnsPost`` 로 변환한다 (레거시)."""
        post_id = row.get("post_id")
        if not post_id:
            return None

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
        """여러 날짜 형식을 방어적으로 파싱한다 (레거시 HTML 경로)."""
        if not raw_date:
            return None
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
    """종목토론방 HTML 을 행 단위로 파싱하는 내부 파서 (레거시).

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

        if "no=" in href:
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            if "no" in params:
                self._current_row["post_id"] = params["no"][0]
            if title:
                self._current_row["title"] = title.strip()

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
            self._current_row["text"] = text
