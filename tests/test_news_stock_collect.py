"""news_stock_collect 회귀 테스트 (DB·네트워크 없음 — 실측 RSS 문자열 + 가짜 커서).

스펙 docs/spec_news_stock_pilot.md §3 의 5개:
  ① RSS XML 파싱(제목·링크·pubDate — 실측 샘플 문자열 사용)
  ② 정밀도 가드(제목에 종목명 없으면 rejected)
  ③ 짧은 이름은 독립 토큰만 허용('남성' ⊂ '남성복' 불통과)
  ④ 멱등(같은 URL 2회 → 1행)
  ⑤ 커서 저장/재개
"""
import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "scripts"))

import news_stock_collect as nsc  # noqa: E402

# ── ① 실측 샘플: 2026-11-19 Google News RSS 실호출 응답의 <item> 3개를 **원문 그대로**
# 붙인 것. 조회 명령(근거):
#   curl -s -A "Mozilla/5.0 ..." "https://news.google.com/rss/search?q=현대차+after:2026-11-05+before:2026-11-18&hl=ko&gl=KR&ceid=KR:ko"
# 제목 1은 '현대차'가 연속 부분문자열이 아닌 **불통과 실측** 제목이고, 2·3은 통과 제목이다.
ITEM_0 = """<item><title>현대자동차그룹, 오픈소스 프로젝트의 시작으로 소프트웨어 기술의 가능성을 넓히다 | 저널 | 현대자동차그룹 - Hyundai Motor Group</title><link>https://news.google.com/rss/articles/CBMipAFBVV95cUxQWFRmMENUVElBZ0dZVEhZcm9LbmVpYThFc1MzVi1KN2Z6UF93NlV3VzFfQkpQVjVyRVZBVTNhZ2hqSFh0c0wyNjFKcW1qcjFWMUJXYW41TGVZbUx3OENXbWs1ZWlzZ2EzdE5STktraW03Z09HZDVkN0dhandhMTE0WmNCd1N4Y1J4aUR2Yy1qeEZfUWRmRUducUVwQWdlTjluRDRTaQ?oc=5</link><guid isPermaLink="false">CBMipAFBVV95cUxQWFRmMENUVElBZ0dZVEhZcm9LbmVpYThFc1MzVi1KN2Z6UF93NlV3VzFfQkpQVjVyRVZBVTNhZ2hqSFh0c0wyNjFKcW1qcjFWMUJXYW41TGVZbUx3OENXbWs1ZWlzZ2EzdE5STktraW03Z09HZDVkN0dhandhMTE0WmNCd1N4Y1J4aUR2Yy1qeEZfUWRmRUducUVwQWdlTjluRDRTaQ</guid><pubDate>Thu, 12 Nov 2026 06:55:00 GMT</pubDate><description>&lt;a href="https://news.google.com/rss/articles/CBMipAFBVV95cUxQWFRmMENUVElBZ0dZVEhZcm9LbmVpYThFc1MzVi1KN2Z6UF93NlV3VzFfQkpQVjVyRVZBVTNhZ2hqSFh0c0wyNjFKcW1qcjFWMUJXYW41TGVZbUx3OENXbWs1ZWlzZ2EzdE5STktraW03Z09HZDVkN0dhandhMTE0WmNCd1N4Y1J4aUR2Yy1qeEZfUWRmRUducUVwQWdlTjluRDRTaQ?oc=5" target="_blank"&gt;현대자동차그룹, 오픈소스 프로젝트의 시작으로 소프트웨어 기술의 가능성을 넓히다 | 저널 | 현대자동차그룹&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Hyundai Motor Group&lt;/font&gt;</description><source url="https://www.hyundaimotorgroup.com">Hyundai Motor Group</source></item>"""

ITEM_1 = """<item><title>현대차 노조가 ‘아반떼 가격 논란’에 쓴소리한 이유는? - 조선일보</title><link>https://news.google.com/rss/articles/CBMif0FVX3lxTE9YYUFPUVl5OHhXNmFvYkFaQUVSQkZad0FqM2JGcFcyMEc5clBuY3IybWhlT0MyQnB5VEdPbkI2M3pYTFhUNWtkYjZYa1REOFJiVG1FeVhHak11OVFwMkR2RkNhd05FdDkxX2YyY2hYbHBmQ1BjQWJ0ZzEyTk9wZ00?oc=5</link><guid isPermaLink="false">CBMif0FVX3lxTE9YYUFPUVl5OHhXNmFvYkFaQUVSQkZad0FqM2JGcFcyMEc5clBuY3IybWhlT0MyQnB5VEdPbkI2M3pYTFhUNWtkYjZYa1REOFJiVG1FeVhHak11OVFwMkR2RkNhd05FdDkxX2YyY2hYbHBmQ1BjQWJ0ZzEyTk9wZ00</guid><pubDate>Wed, 11 Nov 2026 05:57:30 GMT</pubDate><description>&lt;a href="https://news.google.com/rss/articles/CBMif0FVX3lxTE9YYUFPUVl5OHhXNmFvYkFaQUVSQkZad0FqM2JGcFcyMEc5clBuY3IybWhlT0MyQnB5VEdPbkI2M3pYTFhUNWtkYjZYa1REOFJiVG1FeVhHak11OVFwMkR2RkNhd05FdDkxX2YyY2hYbHBmQ1BjQWJ0ZzEyTk9wZ00?oc=5" target="_blank"&gt;현대차 노조가 ‘아반떼 가격 논란’에 쓴소리한 이유는?&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;조선일보&lt;/font&gt;</description><source url="https://www.chosun.com">조선일보</source></item>"""

ITEM_2 = """<item><title>현대차그룹-삼성, ‘원팀’으로 미래 첨단 산업 선점 | 뉴스 | 현대자동차그룹 - Hyundai Motor Group</title><link>https://news.google.com/rss/articles/CBMipgFBVV95cUxQOHV1NDlzckQ2QjNyUFh3UXlFWkROM0EtN2RqRUxsZ0RMZGxlcU1kV1J6Z2VvMTZTZDNtaUhsX09UVzdaS1htclg1QVM3aHdZRUx5RnhuY0FaUlRqZk83Qk1LR0ItSnVjeVpCNFlhR1F4NV9nR19UaExIMTI0anBFZzRkbVN4bWxFcXJUeklNb0RPT1gzUlVZODlLMS0xVzVIT0lSVzRB?oc=5</link><guid isPermaLink="false">CBMipgFBVV95cUxQOHV1NDlzckQ2QjNyUFh3UXlFWkROM0EtN2RqRUxsZ0RMZGxlcU1kV1J6Z2VvMTZTZDNtaUhsX09UVzdaS1htclg1QVM3aHdZRUx5RnhuY0FaUlRqZk83Qk1LR0ItSnVjeVpCNFlhR1F4NV9nR19UaExIMTI0anBFZzRkbVN4bWxFcXJUeklNb0RPT1gzUlVZODlLMS0xVzVIT0lSVzRB</guid><pubDate>Mon, 09 Nov 2026 08:00:00 GMT</pubDate><description>&lt;a href="https://news.google.com/rss/articles/CBMipgFBVV95cUxQOHV1NDlzckQ2QjNyUFh3UXlFWkROM0EtN2RqRUxsZ0RMZGxlcU1kV1J6Z2VvMTZTZDNtaUhsX09UVzdaS1htclg1QVM3aHdZRUx5RnhuY0FaUlRqZk83Qk1LR0ItSnVjeVpCNFlhR1F4NV9nR19UaExIMTI0anBFZzRkbVN4bWxFcXJUeklNb0RPT1gzUlVZODlLMS0xVzVIT0lSVzRB?oc=5" target="_blank"&gt;현대차그룹-삼성, ‘원팀’으로 미래 첨단 산업 선점 | 뉴스 | 현대자동차그룹&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Hyundai Motor Group&lt;/font&gt;</description><source url="https://www.hyundaimotorgroup.com">Hyundai Motor Group</source></item>"""

FEED_XML = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            "<title>현대차 - Google 뉴스</title>" + ITEM_0 + ITEM_1 + ITEM_2
            + "</channel></rss>")


# ── ① RSS XML 파싱 (실측 샘플 문자열) ───────────────────────────────────────
def test_parse_rss_google_news():
    entries = nsc.parse_rss(FEED_XML.encode("utf-8"))
    assert len(entries) == 3
    assert entries[0]["title"].startswith("현대자동차그룹, 오픈소스")
    assert entries[0]["published"] == dt.datetime(2026, 11, 12, 6, 55,
                                                  tzinfo=dt.timezone.utc)
    assert entries[0]["source"] == "Hyundai Motor Group"
    assert entries[1]["title"].startswith("현대차 노조가")
    assert entries[1]["source"] == "조선일보"
    # 링크: Google 리다이렉트 URL 이 그대로 나온다
    for e in entries:
        assert e["url"].startswith("https://news.google.com/rss/articles/")
    # pubDate 없는 item 은 published=None (죽지 않는다)
    no_date = "<item><title>날짜 없음</title><link>https://x.example/1</link></item>"
    parsed = nsc.parse_rss(
        ('<rss><channel>' + no_date + "</channel></rss>").encode("utf-8"))
    assert parsed[0]["published"] is None


# ── ② 정밀도 가드: 제목에 종목명이 그대로 없으면 rejected ─────────────────────
def test_precision_guard_rejects_without_name():
    name = nsc.normalize_stock_name("현대차")   # 길이 3 → 부분문자열 규칙
    ok = {"title": "현대차 노조가 ‘아반떼 가격 논란’에 쓴소리한 이유는?", "url": "https://x/1"}
    assert nsc.title_matches(ok["title"], name)
    # 실측 불통과 제목: '현대자동차그룹'에는 '현대차'가 연속으로 없다
    near = "현대자동차그룹, 오픈소스 프로젝트의 시작으로 소프트웨어 기술의 가능성을 넓히다"
    assert not nsc.title_matches(near, name)
    # 아예 없는 이름
    assert not nsc.title_matches("삼성전자 실적 발표", name)
    # 공백은 정규화로 무시된다(스펙 정규화: 공백 제거)
    assert nsc.title_matches("현대 차 노조 발표", name)


# ── ③ 짧은 이름은 독립 토큰만 허용 ('남성' ⊂ '남성복' 불통과) ─────────────────
def test_short_name_requires_independent_token():
    name = nsc.normalize_stock_name("남성")     # 길이 2 → 독립 토큰 규칙
    assert nsc.match_kind(name) == "token"
    assert nsc.title_matches("남성복 시장 성장", name) is False   # 스펙 예: 불통과
    assert nsc.title_matches("남성 상의 출시", name) is True      # 공백 경계 → 통과
    assert nsc.title_matches("남성, 여성 소비 조사", name) is True  # 구두점 경계 → 통과
    assert nsc.title_matches("청년남성 취업률", name) is False     # 한글 인접 → 불통과
    # 영문 짧은 이름(패널 실측 'YW')도 같은 경계 규칙
    yw = nsc.normalize_stock_name("YW")
    assert nsc.title_matches("YW 주가 급등", yw) is True
    assert nsc.title_matches("AYW 주가 급등", yw) is False        # 영문 인접 → 불통과
    assert nsc.title_matches("YW가 급등", yw) is False            # 한글 인접 → 불통과


# ── ④ 멱등: 같은 (종목,URL) 2회 삽입 → 1행 ───────────────────────────────────
class _FakeCur:
    """INSERT INTO news_stock_mentions 만 흉내 — PK 충돌 시 rowcount 0."""

    def __init__(self):
        self.rows = {}
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.rowcount = 0
        if "INSERT INTO news_stock_mentions" in sql:
            key = (params[0], params[1])
            if key not in self.rows:
                self.rows[key] = params
                self.rowcount = 1

    def close(self):
        pass


def test_insert_idempotent_same_url():
    cur = _FakeCur()
    args = ("000120", "https://news.google.com/rss/articles/AAA", "제목", None, "조선일보", "name")
    assert nsc.insert_mention(cur, *args) == 1    # 첫 삽입
    assert nsc.insert_mention(cur, *args) == 0    # 같은 URL 재삽입 → 충돌 무시
    assert len(cur.rows) == 1                     # 실제 행은 1개
    # 다른 URL 은 삽입된다
    assert nsc.insert_mention(cur, "000120", "https://news.google.com/rss/articles/BBB",
                              "제목2", None, "조선일보", "name") == 1
    assert len(cur.rows) == 2


# ── ⑤ 커서 저장/재개 ─────────────────────────────────────────────────────────
@pytest.fixture()
def cursor(tmp_path, monkeypatch):
    path = tmp_path / "cursor.json"
    monkeypatch.setattr(nsc, "CURSOR_PATH", str(path))
    return path


def _key():
    return nsc._cursor_key("reports/panel_codes.txt", ["000120"], dt.date(2026, 8, 19),
                           dt.date(2026, 11, 18), 14)


def test_cursor_save_resume(cursor):
    key = _key()
    assert nsc.load_cursor(key) == {}                          # 처음엔 커서 없음
    nsc.save_cursor(key, {"000120": dt.date(2026, 11, 18)}, 200)
    assert nsc.load_cursor(key) == {"000120": dt.date(2026, 11, 18)}
    raw = json.loads(cursor.read_text(encoding="utf-8"))
    assert raw["total_codes"] == 200
    # 인자(키)가 바뀌면 커서를 무시한다 — 다른 작업의 진도를 물려받지 않는다
    other = nsc._cursor_key("reports/panel_codes.txt", ["000120"], dt.date(2026, 8, 19),
                            dt.date(2026, 11, 18), 7)          # 창 크기만 달라도
    assert nsc.load_cursor(other) == {}
    other_codes = nsc._cursor_key("reports/panel_codes.txt", ["000120", "000590"],
                                  dt.date(2026, 8, 19), dt.date(2026, 11, 18), 14)
    assert nsc.load_cursor(other_codes) == {}                  # 코드 목록이 달라도
    # 깨진 커서 파일도 예외 없이 무시된다
    cursor.write_text("{ 깨진 json", encoding="utf-8")
    assert nsc.load_cursor(key) == {}
    # 지우면 처음부터(재개 지점 없음)
    nsc.save_cursor(key, {"000120": dt.date(2026, 11, 18)}, 200)
    nsc.clear_cursor()
    assert not cursor.exists()
    assert nsc.load_cursor(key) == {}
