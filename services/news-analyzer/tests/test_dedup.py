"""Tests for content-based dedup (title hash + body Jaccard)."""
from app.models.schemas import Article
from app.normalization.dedup import dedupe, JACCARD_THRESHOLD


def _article(title, content, source="media"):
    return Article(
        source=source,
        title=title,
        content=content,
        url=f"https://example.com/{source}/{abs(hash(title))}",
        published_at=None,
    )


class TestDedupe:
    def test_identical_title_across_media_kept_once(self):
        body = "삼성전자가 2분기 실적을 발표했습니다. 영업이익이 시장 예상을 크게 웃돌았습니다."
        a1 = _article("삼성전자 2분기 실적 발표", body, source="한국경제")
        a2 = _article("삼성전자 2분기 실적 발표", body, source="매일경제")
        unique, dropped = dedupe([a1, a2])
        assert len(unique) == 1
        assert len(dropped) == 1
        assert unique[0] is a1  # first occurrence kept

    def test_similar_body_across_media_kept_once(self):
        # Same story, slightly different wording -> high Jaccard.
        body1 = "삼성전자가 2분기 실적을 발표했습니다. 영업이익이 시장 예상을 크게 웃돌았습니다."
        body2 = "삼성전자가 2분기 실적을 발표했습니다. 영업이익이 시장 예상을 크게 웃돌았습니다. 추가 설명입니다."
        a1 = _article("삼성전자 실적", body1, source="한국경제")
        a2 = _article("삼성전자 2분기 실적", body2, source="이데일리")
        unique, dropped = dedupe([a1, a2])
        assert len(unique) == 1
        assert len(dropped) == 1

    def test_completely_different_kept_both(self):
        a1 = _article("삼성전자 실적 발표", "삼성전자가 2분기 실적을 발표했습니다. 영업이익이 증가했습니다.")
        a2 = _article("현대차 신차 출시", "현대차가 새로운 전기차 모델을 출시했습니다. 주행거리가 늘었습니다.")
        unique, dropped = dedupe([a1, a2])
        assert len(unique) == 2
        assert len(dropped) == 0

    def test_empty_input(self):
        unique, dropped = dedupe([])
        assert unique == []
        assert dropped == []

    def test_three_duplicates_kept_one(self):
        body = "동일한 기사 본문입니다. 충분히 긴 내용으로 구성되어 있습니다."
        a1 = _article("기사 제목", body, source="A")
        a2 = _article("기사 제목", body, source="B")
        a3 = _article("기사 제목", body, source="C")
        unique, dropped = dedupe([a1, a2, a3])
        assert len(unique) == 1
        assert len(dropped) == 2


class TestThreshold:
    def test_threshold_constant_exposed(self):
        assert JACCARD_THRESHOLD == 0.6
