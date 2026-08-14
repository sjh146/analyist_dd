"""Tests for article normalization (HTML strip, NFKC, KST, short-body discard)."""
from datetime import datetime, timezone, timedelta

from app.models.schemas import Article
from app.normalization.normalizer import (
    normalize_article,
    normalize_text,
    normalize_datetime,
    MIN_CONTENT_LENGTH,
    KST,
)


def _article(**kwargs):
    defaults = dict(
        source="test",
        title="테스트 제목",
        content="이것은 충분히 긴 정상적인 뉴스 기사 본문입니다. 최소 길이를 넘는 내용이 필요합니다.",
        url="https://example.com/1",
        published_at=None,
    )
    defaults.update(kwargs)
    return Article(**defaults)


class TestHtmlStrip:
    def test_removes_html_tags(self):
        a = _article(content="<p>삼성전자가 <b>반도체</b> 호황을 맞았다고 발표했습니다. 이 기사는 충분히 긴 본문입니다.</p>")
        norm = normalize_article(a)
        assert norm is not None
        assert "<p>" not in norm.content
        assert "<b>" not in norm.content
        assert "삼성전자가 반도체 호황을 맞았다고 발표했습니다." in norm.content

    def test_removes_html_from_title(self):
        a = _article(title="<h1>삼성전자 실적</h1>")
        norm = normalize_article(a)
        assert norm is not None
        assert norm.title == "삼성전자 실적"


class TestNfkc:
    def test_fullwidth_to_halfwidth(self):
        a = _article(content="삼성전자 주가가 ＡＢＣ１２３으로 표기된 기사 본문입니다. 충분히 긴 내용입니다.")
        norm = normalize_article(a)
        assert norm is not None
        assert "Ａ" not in norm.content
        assert "ABC123" in norm.content

    def test_whitespace_collapsed(self):
        a = _article(content="삼성전자\n\n\n주가가\n  급등했습니다.  이 기사는 충분히 긴 본문입니다.")
        norm = normalize_article(a)
        assert norm is not None
        assert "\n" not in norm.content
        assert "  " not in norm.content


class TestKstConversion:
    def test_utc_aware_converted_to_kst(self):
        utc = datetime(2026, 8, 14, 0, 0, 0, tzinfo=timezone.utc)
        a = _article(published_at=utc)
        norm = normalize_article(a)
        assert norm is not None
        assert norm.published_at is not None
        assert norm.published_at.tzinfo is not None
        assert norm.published_at.utcoffset() == timedelta(hours=9)
        assert norm.published_at.hour == 9  # 00:00 UTC -> 09:00 KST

    def test_naive_datetime_kept(self):
        naive = datetime(2026, 8, 14, 12, 30, 0)
        a = _article(published_at=naive)
        norm = normalize_article(a)
        assert norm is not None
        assert norm.published_at == naive
        assert norm.published_at.tzinfo is None

    def test_normalize_datetime_none(self):
        assert normalize_datetime(None) is None


class TestShortBodyDiscard:
    def test_short_body_discarded(self):
        a = _article(content="짧은 본문")
        assert len(a.content) < MIN_CONTENT_LENGTH
        assert normalize_article(a) is None

    def test_empty_content_discarded(self):
        a = _article(content="")
        assert normalize_article(a) is None

    def test_none_content_discarded(self):
        a = _article(content=None)
        assert normalize_article(a) is None

    def test_html_only_content_discarded(self):
        # After stripping tags, nothing remains -> invalid.
        a = _article(content="<p></p><div></div>")
        assert normalize_article(a) is None

    def test_valid_body_kept(self):
        a = _article()
        norm = normalize_article(a)
        assert norm is not None
        assert norm.source == a.source
        assert norm.url == a.url


class TestNormalizeText:
    def test_empty(self):
        assert normalize_text("") == ""
        assert normalize_text(None) == ""
