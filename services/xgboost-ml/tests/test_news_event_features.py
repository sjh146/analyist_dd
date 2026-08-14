"""Tests for NewsEventFeatures."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.feature_engine.news_event_features import NewsEventFeatures


@pytest.fixture
def nef():
    return NewsEventFeatures()


@pytest.fixture
def mock_db():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    return conn


# =============================================================================
# market_impact_score
# =============================================================================


class TestMarketImpactScore:
    def test_peak_high_score(self, nef, mock_db):
        """A strong recent surge with high importance/novelty yields a high score."""
        cur = mock_db.cursor.return_value
        # recent stats: 100 articles, importance 50
        # past stats: 7 articles over 7 days -> past_avg = 1.0
        cur.fetchone.side_effect = [
            (100.0, 50.0),  # recent window
            (7.0, 3.0),     # past window
        ]
        cur.fetchall.side_effect = [
            [(0.9, 0.8)],   # avg novelty, avg importance
        ]
        result = nef.market_impact_score("005930", db_conn=mock_db)
        score = result["market_impact_score"]
        assert score > 0.0
        # surge = 100 / 1.0 = 100; weight = 1 + 50 = 51; ni = 1 + 0.9 + 0.8 = 2.7
        assert score == pytest.approx(100.0 * 51.0 * 2.7)

    def test_calm_returns_zero(self, nef, mock_db):
        """No recent activity yields a score of 0.0."""
        cur = mock_db.cursor.return_value
        cur.fetchone.side_effect = [
            (0.0, 0.0),  # recent window empty
            (7.0, 3.0),  # past window
        ]
        result = nef.market_impact_score("005930", db_conn=mock_db)
        assert result["market_impact_score"] == 0.0

    def test_no_db_conn(self, nef):
        result = nef.market_impact_score("005930")
        assert result["market_impact_score"] == 0.0

    def test_never_negative(self, nef, mock_db):
        """Score is clamped to >= 0.0 even with degenerate inputs."""
        cur = mock_db.cursor.return_value
        cur.fetchone.side_effect = [
            (5.0, 0.0),  # recent
            (0.0, 0.0),  # past -> past_avg 0 -> huge surge, still positive
        ]
        cur.fetchall.side_effect = [
            [(0.0, 0.0)],  # novelty/importance
        ]
        result = nef.market_impact_score("005930", db_conn=mock_db)
        assert result["market_impact_score"] >= 0.0


# =============================================================================
# event_<type>_5d
# =============================================================================


class TestEventFeatures5d:
    def test_returns_all_event_features(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [
            ("실적발표", 2),
            ("M&A", 1),
        ]
        result = nef.event_features_5d("005930", db_conn=mock_db)
        assert "event_realized_5d" in result
        assert "event_mna_5d" in result
        assert result["event_realized_5d"] == 2.0
        assert result["event_mna_5d"] == 1.0
        # All 18 taxonomy features present.
        assert len(result) == len(nef.EVENT_TYPE_MAP)

    def test_absent_events_zero(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = []
        result = nef.event_features_5d("005930", db_conn=mock_db)
        assert all(v == 0.0 for v in result.values())

    def test_no_db_conn(self, nef):
        result = nef.event_features_5d("005930")
        assert all(v == 0.0 for v in result.values())

    def test_unknown_event_type_ignored(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [("기타", 5)]
        result = nef.event_features_5d("005930", db_conn=mock_db)
        assert all(v == 0.0 for v in result.values())


# =============================================================================
# theme_exposure_5d
# =============================================================================


class TestThemeExposure:
    def test_counts_distinct_theme_occurrences(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [
            ({"themes": ["AI", "반도체"]},),
            ({"themes": ["AI"]},),
            (None,),
        ]
        result = nef.theme_exposure("005930", db_conn=mock_db)
        # AI appears twice, 반도체 once -> 3 total occurrences.
        assert result["theme_exposure_5d"] == 3.0

    def test_no_themes_returns_zero(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = []
        result = nef.theme_exposure("005930", db_conn=mock_db)
        assert result["theme_exposure_5d"] == 0.0

    def test_no_db_conn(self, nef):
        result = nef.theme_exposure("005930")
        assert result["theme_exposure_5d"] == 0.0


# =============================================================================
# get_all_features
# =============================================================================


class TestGetAllFeatures:
    def test_returns_all_feature_groups(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchone.side_effect = [
            (10.0, 5.0),  # recent stats
            (7.0, 3.0),   # past stats
        ]
        cur.fetchall.side_effect = [
            [(0.5, 0.5)],          # novelty/importance
            [("실적발표", 1)],      # event counts
            [({"themes": ["AI"]},)],  # themes
        ]
        result = nef.get_all_features("005930", db_conn=mock_db)
        assert "market_impact_score" in result
        assert "event_realized_5d" in result
        assert "theme_exposure_5d" in result
        assert result["event_realized_5d"] == 1.0
        assert result["theme_exposure_5d"] == 1.0
        assert result["market_impact_score"] > 0.0

    def test_no_db_conn_all_zero(self, nef):
        result = nef.get_all_features("005930")
        assert result["market_impact_score"] == 0.0
        assert all(v == 0.0 for k, v in result.items() if k.startswith("event_"))
        assert result["theme_exposure_5d"] == 0.0
