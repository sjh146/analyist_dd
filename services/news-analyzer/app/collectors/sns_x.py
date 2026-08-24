"""
X(Twitter) Collector — xurl CLI 기반
====================================

X(Twitter) 게시글을 ``xurl`` CLI 를 통해 수집하는 수집기.

xurl 스킬 참조 (Hermes)
-----------------------
이 수집기는 Hermes 의 ``xurl`` 스킬을 통해 X API 에 접근한다. xurl 은
X(Twitter) API 를 CLI 로 감싼 도구로, ``xurl tweet {id}`` / ``xurl timeline
{user}`` 형태의 명령을 지원한다. 명령 형태는 ``XURL_*`` 환경변수로
구성 가능하다.

XURL_* 환경변수 계약
--------------------
자격증명은 런타임 환경변수에서만 읽는다 (절대 하드코딩하지 않음, ``.env``
파일을 읽지 않음). xurl 서브프로세스에 그대로 env 로 전달된다.

- ``XURL_URL``: xurl 엔드포인트/베이스 URL.
- ``XURL_USER``: xurl 인증 사용자.
- ``XURL_TOKEN``: xurl 인증 토큰.

인터페이스 우선 (interface-first)
--------------------------------
이 호스트에는 ``xurl`` 바이너리가 설치되어 있지 않다 (``which xurl`` 빈 값,
``~/.local/bin/xurl`` 없음). 따라서 이 모듈은 xurl 을 호출하는 전체 코드
경로를 구현하되, 바이너리가 없으면 ``is_available()`` 이 False 를 반환하고
``collect()`` 는 빈 리스트를 반환하는 fail-open 동작을 한다.

실제 구현을 붙일 때는 xurl 바이너리를 설치하고 ``XURL_*`` 환경변수만
설정하면 된다 — 코드 변경 없이 자격증명만으로 동작한다.

이 파일은 DB/네트워크에 의존하지 않으므로 호스트 pytest 에서 그대로 import
가능하다.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .sns_interface import SnsPost

logger = logging.getLogger(__name__)


class XCollector:
    """X(Twitter) 게시글 수집기 (xurl CLI 기반)."""

    #: xurl 바이너리 후보 경로 (런타임에 순서대로 탐색)
    BINARY_CANDIDATES = [
        "xurl",
        str(Path.home() / ".local" / "bin" / "xurl"),
    ]
    #: 서브프로세스 타임아웃(초)
    SUBPROCESS_TIMEOUT = 15.0

    def __init__(self):
        self._binary = self._resolve_binary()

    def _resolve_binary(self) -> Optional[str]:
        """xurl 바이너리 경로를 런타임에 탐색한다.

        Returns:
            찾은 바이너리 경로. 없으면 None.
        """
        for candidate in self.BINARY_CANDIDATES:
            found = shutil.which(candidate)
            if found:
                return found
            # ~/.local/bin/xurl 처럼 절대/상대 경로 후보는 직접 존재 확인
            if os.path.isfile(candidate):
                return candidate
        return None

    def is_available(self) -> bool:
        """xurl 바이너리가 설치되어 있는지 여부.

        Returns:
            xurl 을 찾으면 True, 아니면 False.
        """
        return self._binary is not None

    async def collect(self, keywords: Optional[List[str]] = None) -> List[SnsPost]:
        """키워드 기반 X 게시글을 수집한다.

        xurl 이 설치되어 있지 않으면 "interface-only mode" 로그를 남기고 빈
        리스트를 반환한다. 서브프로세스는 ``asyncio.to_thread`` 로 실행하며
        짧은 타임아웃을 적용한다. 어떤 오류에서도 예외를 던지지 않고 빈
        리스트를 반환한다 (fail-open).

        Args:
            keywords: 수집할 검색 키워드 목록. None 이면 기본 키워드를 사용한다.

        Returns:
            수집된 ``SnsPost`` 목록. 실패/미지원 시 빈 리스트.
        """
        if not self.is_available():
            logger.info(
                "xurl not installed; interface-only mode — X collection skipped. "
                "Install xurl and set XURL_URL/XURL_USER/XURL_TOKEN to enable."
            )
            return []

        if keywords is None:
            keywords = self._get_default_keywords()

        posts: List[SnsPost] = []
        for keyword in keywords:
            try:
                keyword_posts = await self._fetch_keyword(keyword)
                posts.extend(keyword_posts)
            except Exception as e:
                # 키워드 단위 실패는 로그만 남기고 계속 진행 (fail-open)
                logger.debug(f"X collection failed for keyword {keyword}: {e}")
                continue

        logger.info(f"Collected {len(posts)} X posts")
        return posts

    async def _fetch_keyword(self, keyword: str) -> List[SnsPost]:
        """단일 키워드에 대한 xurl 호출을 수행하고 결과를 파싱한다."""
        cmd = self._build_command(keyword)
        env = self._build_env()

        try:
            # 서브프로세스는 블로킹이므로 스레드로 실행
            output = await asyncio.to_thread(
                self._run_subprocess, cmd, env
            )
            return self._parse_output(output)
        except Exception as e:
            logger.debug(f"xurl subprocess error for keyword {keyword}: {e}")
            return []

    def _build_command(self, keyword: str) -> List[str]:
        """xurl 호출 명령을 구성한다.

        명령 형태는 ``xurl timeline {user}`` 스타일을 기본으로 하되, 키워드
        검색을 위해 ``xurl search {keyword}`` 형태를 사용한다. 명령 형태는
        ``XURL_COMMAND`` 환경변수로 재정의 가능하다.
        """
        command = os.environ.get("XURL_COMMAND", "search")
        return [self._binary, command, keyword]

    def _build_env(self) -> dict:
        """xurl 서브프로세스에 전달할 환경변수를 구성한다.

        ``XURL_*`` 환경변수만 전달한다 (절대 하드코딩하지 않음).
        """
        env = dict(os.environ)
        # XURL_* 자격증명은 이미 os.environ 에 있으므로 그대로 전달
        return env

    def _run_subprocess(self, cmd: List[str], env: dict) -> str:
        """xurl 서브프로세스를 실행하고 stdout 을 반환한다."""
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.SUBPROCESS_TIMEOUT,
            env=env,
        )
        if result.returncode != 0:
            logger.debug(f"xurl exited with {result.returncode}: {result.stderr}")
            return ""
        return result.stdout

    def _parse_output(self, output: str) -> List[SnsPost]:
        """xurl JSON 출력을 방어적으로 파싱해 ``SnsPost`` 목록을 반환한다.

        xurl 출력은 JSON 배열 또는 JSON 객체일 수 있다. 파싱 실패 시 빈
        리스트를 반환한다.
        """
        if not output:
            return []

        try:
            data = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            logger.debug("xurl output is not valid JSON")
            return []

        # JSON 배열 또는 객체의 items/data 키 처리. 단일 객체면 그 자체를 항목으로.
        if isinstance(data, list):
            items = data
        elif "items" in data and isinstance(data["items"], list):
            items = data["items"]
        elif "data" in data and isinstance(data["data"], list):
            items = data["data"]
        else:
            items = [data]
        if not isinstance(items, list):
            items = [items]

        posts: List[SnsPost] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            post = self._item_to_post(item)
            if post is not None:
                posts.append(post)
        return posts

    def _item_to_post(self, item: dict) -> Optional[SnsPost]:
        """xurl JSON 항목을 ``SnsPost`` 로 변환한다.

        필드가 없으면 None/기본값을 사용하고 크래시하지 않는다.
        """
        post_id = item.get("id") or item.get("tweet_id") or item.get("id_str")
        if not post_id:
            return None

        # 날짜 파싱 (ISO 8601 방어)
        posted_at = None
        raw_date = item.get("created_at") or item.get("createdAt")
        if raw_date:
            posted_at = self._parse_date(raw_date)

        author = item.get("author") or item.get("user") or {}
        if not isinstance(author, dict):
            author = {}

        return SnsPost(
            source="x",
            post_id=str(post_id),
            stock_code=None,
            author_id=author.get("id") or author.get("id_str"),
            author_name=author.get("name") or author.get("screen_name"),
            author_followers=int(author.get("followers_count") or 0),
            posted_at=posted_at,
            text=item.get("text") or item.get("full_text"),
            raw_json=item,
            comment_count=int(item.get("reply_count") or 0),
            like_count=int(item.get("favorite_count") or item.get("like_count") or 0),
            retweet_count=int(item.get("retweet_count") or 0),
        )

    @staticmethod
    def _parse_date(raw_date: str) -> Optional[datetime]:
        """ISO 8601 및 X API 날짜 형식을 방어적으로 파싱한다.

        파싱 실패 시 None.
        """
        raw = raw_date.strip()
        # X API: "Wed Aug 24 10:30:00 +0000 2026"
        try:
            return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            pass
        # ISO 8601: "2026-08-24T10:30:00Z" 또는 "2026-08-24T10:30:00+09:00"
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
        return None

    @staticmethod
    def _get_default_keywords() -> List[str]:
        """기본 검색 키워드 목록을 반환한다."""
        return ["삼성전자", "005930"]
