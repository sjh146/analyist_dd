"""DB-free unit tests for the SNS collectors (Phase A).

Covers:
 1. Naver 종목토론방 HTML 파싱 (픽스처 HTML) -> SnsPost 필드 추출.
 2. 요청 간 딜레이 강제: REQUEST_DELAY >= 0.7 (네이버 자동화 차단 대응) 및
    _rate_limit 이 남은 시간만큼 sleep.
 3. X collector: xurl 미설치 -> is_available() False, collect() [] (fail-open).
    xurl JSON 파싱 방어 (유효/무효 입력).
 4. Provider 인터페이스: 스텁(SecuritiesPlus/NaverCafe) 미지원 -> [] / abstract.
"""
import asyncio
import importlib.util
import os
import sys
from datetime import datetime

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# news-analyzer 모듈은 'app' 패키지 충돌을 피해 고유 네임스페이스로 로드한다.
_loader_spec = importlib.util.spec_from_file_location(
    "sns_news_loader", os.path.join(REPO_ROOT, "tests", "sns_news_loader.py")
)
_loader = importlib.util.module_from_spec(_loader_spec)
sys.modules["sns_news_loader"] = _loader
_loader_spec.loader.exec_module(_loader)
_si, _nb, _sns_x, _ = _loader.load_sns_modules()

NaverBoardCollector = _nb.NaverBoardCollector
XCollector = _sns_x.XCollector
SnsProvider = _si.SnsProvider
SnsPost = _si.SnsPost
SecuritiesPlusProvider = _si.SecuritiesPlusProvider
NaverCafeProvider = _si.NaverCafeProvider


def run(coro):
    return asyncio.run(coro)


# ── 1. Naver 종목토론방 HTML 파싱 ───────────────────────────────────────
def _board_html():
    return """
    <table>
      <tr>
        <td class="title"><a href="/item/board.naver?code=005930&no=12345" title="매수 추천">매수 추천</a></td>
        <td class="pname"><a href="/item/board.naver?userId=hong" title="홍길동">홍길동</a></td>
        <td class="date">2026.08.24 10:30</td>
        <td>삼성전자 매수 상승</td>
      </tr>
      <tr>
        <td class="title"><a href="/item/board.naver?code=005930&no=67890" title="손절">손절</a></td>
        <td class="pname"><a href="/item/board.naver?userId=kim" title="김철수">김철수</a></td>
        <td class="date">2026-08-24 11:00:00</td>
        <td>하락 악재</td>
      </tr>
    </table>
    """


def test_naver_parse_posts_fields():
    collector = NaverBoardCollector()
    posts = collector.parse_board_html(_board_html(), "005930")
    assert len(posts) == 2

    p0 = posts[0]
    assert p0.post_id == "12345"
    assert p0.stock_code == "005930"
    assert p0.source == "naver_board"
    assert p0.author_id == "hong"
    assert p0.author_name == "홍길동"
    assert isinstance(p0.posted_at, datetime)
    # 본문 셀 텍스트 또는 제목이 텍스트로 남는다.
    assert p0.text is not None

    p1 = posts[1]
    assert p1.post_id == "67890"
    assert p1.author_id == "kim"


def test_naver_parse_skips_row_without_post_id():
    html = """
    <table><tr>
      <td class="title"><a href="/item/board.naver" title="무링크">무링크</a></td>
      <td class="pname"><a href="?userId=x">aa</a></td>
      <td class="date">2026.08.24 09:00</td>
      <td>no id</td>
    </tr></table>
    """
    posts = NaverBoardCollector().parse_board_html(html, "005930")
    assert posts == []


def test_naver_parse_empty_and_malformed():
    collector = NaverBoardCollector()
    assert collector.parse_board_html("", "005930") == []
    assert collector.parse_board_html("<broken>", "005930") == []
    assert collector.parse_board_html(None, "005930") == []


# ── 2. 요청 딜레이 (네이버 자동화 차단 대응) ─────────────────────────────
def test_naver_request_delay_at_least_070s():
    assert NaverBoardCollector.REQUEST_DELAY >= 0.7


def test_naver_rate_limit_waits_remaining_time(monkeypatch):
    import time
    collector = NaverBoardCollector()
    # 방금 요청했다 가정 → elapsed ≈ 0 < 0.7 → 남은 딜레이를 sleep 해야 한다.
    collector._last_request = time.monotonic()
    slept = []
    async def fake_sleep(secs):
        slept.append(secs)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    run(collector._rate_limit())
    assert len(slept) == 1
    # 남은 딜레이(0.7 - elapsed)를 sleep. 부동소수점 타이밍 오차 허용(> 0.69).
    assert slept[0] > 0.69


def test_naver_rate_limit_no_wait_after_delay_elapsed(monkeypatch):
    collector = NaverBoardCollector()
    # 마지막 요청이 아주 오래전(_last_request=0) → elapsed >= 0.7 → sleep 없음.
    collector._last_request = 0.0
    slept = []
    async def fake_sleep(secs):
        slept.append(secs)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    run(collector._rate_limit())
    assert slept == []


# ── 3. X collector (xurl 미설치 fail-open) ──────────────────────────────
def test_x_not_available_without_xurl():
    collector = XCollector()
    assert collector.is_available() is False


def test_x_collect_returns_empty_without_xurl():
    collector = XCollector()
    assert run(collector.collect()) == []


def test_x_collect_never_raises_without_xurl():
    collector = XCollector()
    # 키워드를 줘도 미설치면 [] 만 반환하고 예외 없음.
    assert run(collector.collect(["삼성전자", "005930"])) == []


def test_x_parse_output_single_object():
    collector = XCollector()
    raw = json_dumps({"id": "t1", "text": "hello", "user": {"id": "u1",
                                                             "name": "jake"}})
    posts = collector._parse_output(raw)
    assert len(posts) == 1
    assert posts[0].source == "x"
    assert posts[0].post_id == "t1"
    assert posts[0].text == "hello"
    assert posts[0].author_id == "u1"


def test_x_parse_output_list():
    collector = XCollector()
    raw = json_dumps([
        {"tweet_id": "t1", "full_text": "a", "author": {"followers_count": 10}},
        {"id_str": "t2", "text": "b"},
    ])
    posts = collector._parse_output(raw)
    assert len(posts) == 2
    assert posts[0].author_followers == 10


def test_x_parse_output_invalid_json():
    collector = XCollector()
    assert collector._parse_output("not json") == []
    assert collector._parse_output("") == []


def json_dumps(obj):
    import json
    return json.dumps(obj)
def test_sns_provider_is_abstract():
    with pytest.raises(TypeError):
        SnsProvider()


def test_securities_plus_stub_not_available():
    p = SecuritiesPlusProvider()
    assert p.is_available() is False
    assert p.name == "securities_plus"
    assert run(p.fetch(["005930"])) == []


def test_naver_cafe_stub_not_available():
    p = NaverCafeProvider()
    assert p.is_available() is False
    assert p.name == "naver_cafe"
    assert run(p.fetch()) == []


def test_sns_post_defaults():
    p = SnsPost(source="test", post_id="1")
    assert p.stock_code is None
    assert p.author_followers == 0
    assert p.posted_at is None
    assert p.raw_json == {}
    assert p.comment_count == 0
    assert p.like_count == 0
    assert p.retweet_count == 0
