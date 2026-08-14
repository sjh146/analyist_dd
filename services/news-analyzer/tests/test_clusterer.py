"""Tests for event clustering (Phase 3)."""
from datetime import datetime

from app.events.clusterer import cluster, EventCluster, JACCARD_THRESHOLD


def _ev(stock_code, event_type, created_at, importance=0.5,
        sentiment_score=0.0, core_event_text="삼성전자가 2분기 실적을 발표했습니다"):
    return {
        "stock_code": stock_code,
        "event_type": event_type,
        "created_at": created_at,
        "importance": importance,
        "sentiment_score": sentiment_score,
        "core_event_text": core_event_text,
    }


def _dt(hour, minute=0, day=1):
    return datetime(2026, 8, day, hour, minute)


class TestTimeBucketing:
    def test_same_event_within_2h_single_cluster(self):
        events = [
            _ev("005930", "실적발표", _dt(10, 0)),
            _ev("005930", "실적발표", _dt(11, 30)),
        ]
        clusters = cluster(events)
        assert len(clusters) == 1
        assert clusters[0].article_count == 2

    def test_beyond_2h_separate_clusters(self):
        events = [
            _ev("005930", "실적발표", _dt(10, 0)),
            _ev("005930", "실적발표", _dt(13, 0)),
        ]
        clusters = cluster(events)
        assert len(clusters) == 2

    def test_bucket_boundary_2h_exact_split(self):
        # 12:00 -> bucket 12-14, 14:00 -> bucket 14-16 (separate)
        events = [
            _ev("005930", "실적발표", _dt(12, 0)),
            _ev("005930", "실적발표", _dt(14, 0)),
        ]
        clusters = cluster(events)
        assert len(clusters) == 2


class TestTypeSeparation:
    def test_different_event_type_separate(self):
        events = [
            _ev("005930", "실적발표", _dt(10, 0)),
            _ev("005930", "배당", _dt(10, 30)),
        ]
        clusters = cluster(events)
        assert len(clusters) == 2

    def test_different_stock_separate(self):
        events = [
            _ev("005930", "실적발표", _dt(10, 0)),
            _ev("000660", "실적발표", _dt(10, 30)),
        ]
        clusters = cluster(events)
        assert len(clusters) == 2


class TestTextSimilarity:
    def test_similar_text_merged(self):
        events = [
            _ev("005930", "실적발표", _dt(10, 0),
                core_event_text="삼성전자가 2분기 실적을 발표했습니다 영업이익이 증가했습니다"),
            _ev("005930", "실적발표", _dt(10, 30),
                core_event_text="삼성전자가 2분기 실적을 발표했습니다 영업이익이 증가했습니다 추가 설명"),
        ]
        clusters = cluster(events)
        assert len(clusters) == 1

    def test_different_text_separate(self):
        events = [
            _ev("005930", "실적발표", _dt(10, 0),
                core_event_text="삼성전자가 2분기 실적을 발표했습니다 영업이익이 증가했습니다"),
            _ev("005930", "실적발표", _dt(10, 30),
                core_event_text="현대차가 새로운 전기차 모델을 출시했습니다 주행거리가 늘었습니다"),
        ]
        clusters = cluster(events)
        assert len(clusters) == 2


class TestAggregation:
    def test_aggregates(self):
        base = "삼성전자가 2분기 실적을 발표했습니다 영업이익이 증가했습니다"
        events = [
            _ev("005930", "실적발표", _dt(10, 0), importance=0.3,
                sentiment_score=0.2, core_event_text=base),
            _ev("005930", "실적발표", _dt(10, 30), importance=0.9,
                sentiment_score=-0.7, core_event_text=base + " 추가 설명"),
            _ev("005930", "실적발표", _dt(11, 0), importance=0.5,
                sentiment_score=0.4, core_event_text=base + " 시장 반응"),
        ]
        clusters = cluster(events)
        assert len(clusters) == 1
        cl = clusters[0]
        assert cl.article_count == 3
        assert cl.first_article_at == _dt(10, 0)
        assert cl.last_article_at == _dt(11, 0)
        assert abs(cl.total_importance - 1.7) < 1e-6
        assert abs(cl.max_sentiment_abs - 0.7) < 1e-6
        # representative = highest importance article
        assert cl.representative_core_event_text == base + " 추가 설명"

    def test_cluster_key_format(self):
        events = [_ev("005930", "실적발표", _dt(10, 0))]
        clusters = cluster(events)
        assert clusters[0].cluster_key == "005930:실적발표:2026-08-01:10-12"

    def test_empty_input(self):
        assert cluster([]) == []


class TestThreshold:
    def test_threshold_constant_exposed(self):
        assert JACCARD_THRESHOLD == 0.5
