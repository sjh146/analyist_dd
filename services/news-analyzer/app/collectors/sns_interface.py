"""
SNS Provider Interface
======================

SNS 인텔리전스 파이프라인의 추상 Provider 인터페이스.

이 모듈은 로그인 벽(login wall) 또는 비공식 API(unofficial API)로 접근이
제한되는 SNS 소스(증권플러스, 네이버 카페 등)에 대한 공통 계약을 정의한다.

- ``SnsPost``: 모든 SNS 수집기가 반환하는 경량 데이터클래스.
- ``SnsProvider``: 소스별 수집기의 추상 베이스 클래스.
- ``SecuritiesPlusProvider`` / ``NaverCafeProvider``: 현재는 수집 차단 사유를
  기록하고 ``[]`` 를 반환하는 스텁(stub) 구현.

이 파일은 DB/네트워크에 의존하지 않으므로 호스트 pytest 에서 그대로 import
가능하다. ``from app...`` import 패턴을 쓰지 않고 순수 stdlib 만 사용한다.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SnsPost:
    """SNS 게시글 1건을 나타내는 경량 데이터클래스.

    ``sns_posts`` 테이블의 컬럼과 1:1 대응한다. 필드가 없으면 None/기본값을
    사용하며, 파싱 실패 시에도 크래시하지 않도록 방어적으로 설계한다.
    """

    source: str  # 수집 소스 식별자 (예: "naver_board", "x", "securities_plus")
    post_id: str  # 소스 내 고유 게시글 ID
    stock_code: Optional[str] = None  # 관련 종목 코드 (없으면 None)
    author_id: Optional[str] = None  # 작성자 고유 ID
    author_name: Optional[str] = None  # 작성자 표시 이름
    author_followers: int = 0  # 작성자 팔로워 수 (알 수 없으면 0)
    posted_at: Optional[datetime] = None  # 게시 시각
    text: Optional[str] = None  # 게시글 본문/제목
    raw_json: Optional[Dict] = field(default_factory=dict)  # 원본 JSON (디버깅용)
    comment_count: int = 0  # 댓글 수
    like_count: int = 0  # 좋아요 수
    retweet_count: int = 0  # 리트윗/공유 수


class SnsProvider(ABC):
    """SNS 소스 수집기의 추상 베이스 클래스.

    실제 구현체는 ``fetch()`` 와 ``is_available()`` 을 반드시 구현해야 한다.
    모든 구현체는 실패 시 예외를 던지지 않고 ``[]`` 를 반환하는 fail-open
    스타일을 따라야 한다.
    """

    #: 소스 식별자 (예: "securities_plus", "naver_cafe")
    name: str = "sns"

    @abstractmethod
    async def fetch(self, stock_codes: Optional[List[str]] = None) -> List[SnsPost]:
        """지정된 종목들에 대한 SNS 게시글을 수집한다.

        Args:
            stock_codes: 수집할 종목 코드 목록. None 이면 기본 종목을 사용한다.

        Returns:
            수집된 ``SnsPost`` 목록. 실패/미지원 시 빈 리스트.
        """
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """이 소스가 현재 수집 가능한지 여부.

        Returns:
            수집 가능하면 True, 아니면 False.
        """
        raise NotImplementedError


class SecuritiesPlusProvider(SnsProvider):
    """증권플러스(Securities Plus) 수집기 스텁.

    증권플러스는 로그인 벽(login wall)과 비공식 API(unofficial API)로 접근이
    제한되어 있어 현재는 수집할 수 없다. ``is_available()`` 은 항상 False 를
    반환하고, ``fetch()`` 는 빈 리스트를 반환한다.
    """

    name = "securities_plus"

    SUPPORT_NOTE = """
    증권플러스 수집기 교체 가이드
    -----------------------------
    현재는 로그인 벽 + 비공식 API 로 인해 수집이 차단된 스텁 상태다.

    실제 구현을 붙일 때는 다음 구조를 따르면 된다:
      1. SnsProvider 를 상속받아 fetch()/is_available() 을 구현한다.
      2. is_available() 은 자격증명(credential) 존재 여부를 확인해 True/False 를
         반환한다 (예: os.environ 에 API 토큰이 있는지).
      3. fetch() 는 stock_codes 를 순회하며 각 종목의 게시글을 수집하고
         SnsPost 로 변환해 반환한다. 실패 시 예외를 던지지 말고 [] 를 반환한다.
      4. 자격증명은 절대 하드코딩하지 말고 런타임 환경변수에서만 읽는다.
      5. rate limiting / 재시도 / 타임아웃은 sns_naver_board.py 의 패턴을 따른다.
    """

    async def fetch(self, stock_codes: Optional[List[str]] = None) -> List[SnsPost]:
        """증권플러스 게시글 수집 (현재 미지원).

        로그인 벽 + 비공식 API 로 인해 수집이 차단되어 빈 리스트를 반환한다.
        """
        logger.debug(
            "SecuritiesPlus collection blocked: login wall + unofficial API "
            "(interface-only mode)"
        )
        return []

    def is_available(self) -> bool:
        """증권플러스 수집 가능 여부 (항상 False)."""
        return False


class NaverCafeProvider(SnsProvider):
    """네이버 카페(Naver Cafe) 수집기 스텁.

    네이버 카페는 로그인 벽(login wall)과 비공식 API(unofficial API)로 접근이
    제한되어 있어 현재는 수집할 수 없다. ``is_available()`` 은 항상 False 를
    반환하고, ``fetch()`` 는 빈 리스트를 반환한다.
    """

    name = "naver_cafe"

    SUPPORT_NOTE = """
    네이버 카페 수집기 교체 가이드
    -----------------------------
    현재는 로그인 벽 + 비공식 API 로 인해 수집이 차단된 스텁 상태다.

    실제 구현을 붙일 때는 다음 구조를 따르면 된다:
      1. SnsProvider 를 상속받아 fetch()/is_available() 을 구현한다.
      2. is_available() 은 자격증명(credential) 존재 여부를 확인해 True/False 를
         반환한다 (예: os.environ 에 세션 쿠키가 있는지).
      3. fetch() 는 stock_codes 를 순회하며 각 종목의 게시글을 수집하고
         SnsPost 로 변환해 반환한다. 실패 시 예외를 던지지 말고 [] 를 반환한다.
      4. 자격증명은 절대 하드코딩하지 말고 런타임 환경변수에서만 읽는다.
      5. rate limiting / 재시도 / 타임아웃은 sns_naver_board.py 의 패턴을 따른다.
    """

    async def fetch(self, stock_codes: Optional[List[str]] = None) -> List[SnsPost]:
        """네이버 카페 게시글 수집 (현재 미지원).

        로그인 벽 + 비공식 API 로 인해 수집이 차단되어 빈 리스트를 반환한다.
        """
        logger.debug(
            "NaverCafe collection blocked: login wall + unofficial API "
            "(interface-only mode)"
        )
        return []

    def is_available(self) -> bool:
        """네이버 카페 수집 가능 여부 (항상 False)."""
        return False
