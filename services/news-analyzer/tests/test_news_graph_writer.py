"""Tests for NewsGraphWriter (Phase 7)."""
from datetime import date, datetime

from app.events.clusterer import EventCluster
from app.graph.news_graph_writer import NewsGraphWriter


class _FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self):
        self.session_obj = _FakeSession()

    def session(self):
        return self.session_obj


def _cluster(stock_code="005930", event_type="실적발표", event_date=None,
             cluster_key=None, importance=1.0, text="삼성전자가 실적을 발표"):
    d = event_date or date(2026, 8, 1)
    return EventCluster(
        stock_code=stock_code,
        event_type=event_type,
        event_date=d,
        time_bucket="10-12",
        cluster_key=cluster_key or f"{stock_code}:{event_type}:{d}:10-12",
        article_count=1,
        first_article_at=datetime(2026, 8, 1, 10, 0),
        last_article_at=datetime(2026, 8, 1, 10, 0),
        total_importance=importance,
        max_sentiment_abs=0.5,
        representative_core_event_text=text,
    )


class TestWriteEvents:
    def test_merges_event_and_has_event(self):
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        cl = _cluster()
        n = writer.write_events([cl])
        assert n == 1
        cypher, params = driver.session_obj.calls[0]
        assert "MERGE (e:Event {event_id: $event_id})" in cypher
        assert "MERGE (s)-[:HAS_EVENT]->(e)" in cypher
        assert params["event_id"] == cl.cluster_key
        assert params["type"] == "실적발표"
        assert params["stock_code"] == "005930"
        assert params["impact_score"] == 1.0

    def test_empty_events_no_calls(self):
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        assert writer.write_events([]) == 0
        assert driver.session_obj.calls == []


class TestWriteThemes:
    def test_merges_theme_and_has_theme(self):
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        n = writer.write_themes([("005930", "AI/인공지능")])
        assert n == 1
        cypher, params = driver.session_obj.calls[0]
        assert "MERGE (t:Theme {name: $theme_name})" in cypher
        assert "MERGE (s)-[:HAS_THEME]->(t)" in cypher
        assert params == {"theme_name": "AI/인공지능", "stock_code": "005930"}

    def test_skips_empty_theme(self):
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        assert writer.write_themes([("005930", "")]) == 0
        assert driver.session_obj.calls == []


class TestWriteImpact:
    def test_merges_impact_and_has_impact(self):
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        n = writer.write_impact([{"stock_code": "005930", "score": 2.5,
                                  "date": date(2026, 8, 1)}])
        assert n == 1
        cypher, params = driver.session_obj.calls[0]
        assert "MERGE (i:ImpactScore {stock_code: $stock_code, date: $date})" in cypher
        assert "MERGE (s)-[:HAS_IMPACT]->(i)" in cypher
        assert params["score"] == 2.5
        assert params["date"] == "2026-08-01"

    def test_skips_missing_score(self):
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        assert writer.write_impact([{"stock_code": "005930", "date": "2026-08-01"}]) == 0
        assert driver.session_obj.calls == []


class TestWriteCoOccurs:
    def test_merges_co_occurs(self):
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        n = writer.write_co_occurs([("ev1", "ev2")])
        assert n == 1
        cypher, params = driver.session_obj.calls[0]
        assert "MERGE (a)-[:CO_OCCURS]->(b)" in cypher
        assert params == {"a": "ev1", "b": "ev2"}

    def test_skips_self_pair(self):
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        assert writer.write_co_occurs([("ev1", "ev1")]) == 0
        assert driver.session_obj.calls == []


class TestWriteCoEvent:
    def test_merges_co_event(self):
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        n = writer.write_co_event([("005930", "000660")])
        assert n == 1
        cypher, params = driver.session_obj.calls[0]
        assert "MERGE (a)-[:CO_EVENT]->(b)" in cypher
        assert params == {"a": "005930", "b": "000660"}


class TestExistingRelationshipsPreserved:
    def test_uses_merge_not_delete(self):
        """Writer must only MERGE, never DELETE existing nodes/relationships."""
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        writer.write_events([_cluster()])
        writer.write_themes([("005930", "AI")])
        writer.write_impact([{"stock_code": "005930", "score": 1.0,
                              "date": date(2026, 8, 1)}])
        writer.write_co_occurs([("ev1", "ev2")])
        writer.write_co_event([("005930", "000660")])
        for cypher, _ in driver.session_obj.calls:
            assert "DELETE" not in cypher
            assert "DETACH" not in cypher
            assert "MERGE" in cypher

    def test_sentiment_of_path_untouched(self):
        """SENTIMENT_OF (save_sentiment_relationship) is separate and preserved."""
        driver = _FakeDriver()
        writer = NewsGraphWriter(driver=driver)
        writer.write_events([_cluster()])
        for cypher, _ in driver.session_obj.calls:
            assert "SENTIMENT_OF" not in cypher
            assert "Sentiment" not in cypher


class TestNoDriver:
    def test_no_driver_returns_zero(self):
        writer = NewsGraphWriter(driver=None)
        writer._driver = None
        assert writer.write_events([_cluster()]) == 0
