"""Tests for AlternativeFeatures."""

import pytest
import numpy as np
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


@pytest.fixture
def af():
    from app.feature_engine.alt_features import AlternativeFeatures
    return AlternativeFeatures()


@pytest.fixture
def mock_db():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    return conn


def make_rows(dates, values):
    """Build list of (date, value) tuples matching SQL cursor fetchall."""
    return list(zip(dates, values))


class TestSentimentSurge:
    def test_positive_surge(self, af, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [
            (date(2024, 6, 4), 0.8),
            (date(2024, 6, 3), 0.2),
            (date(2024, 6, 2), 0.1),
        ]
        result = af.sentiment_surge("005930", "2024-06-04", lookback=3, threshold=2.0, db_conn=mock_db)
        assert result["sentiment_surge"] == 1.0

    def test_negative_surge(self, af, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [
            (date(2024, 6, 4), -1.0),
            (date(2024, 6, 3), 0.9),
            (date(2024, 6, 2), 0.85),
        ]
        result = af.sentiment_surge("005930", "2024-06-04", lookback=3, threshold=2.0, db_conn=mock_db)
        assert result["sentiment_surge"] == -1.0

    def test_no_surge(self, af, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [
            (date(2024, 6, 4), 0.5),
            (date(2024, 6, 3), 0.4),
            (date(2024, 6, 2), 0.45),
        ]
        result = af.sentiment_surge("005930", "2024-06-04", lookback=3, threshold=2.0, db_conn=mock_db)
        assert result["sentiment_surge"] == 0.0

    def test_insufficient_data(self, af, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [
            (date(2024, 6, 4), 0.5),
        ]
        result = af.sentiment_surge("005930", "2024-06-04", db_conn=mock_db)
        assert result["sentiment_surge"] == 0.0

    def test_no_db_conn(self, af):
        result = af.sentiment_surge("005930", "2024-06-04")
        assert result["sentiment_surge"] == 0.0

    def test_none_values(self, af, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [
            (date(2024, 6, 4), None),
            (date(2024, 6, 3), None),
            (date(2024, 6, 2), None),
        ]
        result = af.sentiment_surge("005930", "2024-06-04", db_conn=mock_db)
        assert result["sentiment_surge"] == 0.0


class TestCrossAssetCorrelation:
    def test_returns_correlations(self, af, mock_db):
        cur = mock_db.cursor.return_value

        base = 100.0
        dates_20 = [date(2024, 5, i) for i in range(1, 22)]
        stock_prices = [(d, base + i * 0.5) for i, d in enumerate(dates_20)]
        stock_prices.reverse()

        def fetchall_side_effect():
            return stock_prices

        macro_dates = [date(2024, 5, i) for i in range(1, 22)]
        fx_rows = [(d, 1300.0 + i * 0.1) for i, d in enumerate(macro_dates)]
        fx_rows.reverse()
        oil_rows = [(d, 70.0 + i * 0.05) for i, d in enumerate(macro_dates)]
        oil_rows.reverse()
        rate_rows = [(d, 3.50) for i, d in enumerate(macro_dates)]
        rate_rows.reverse()

        # market_data query -> stock prices, then 3 macro queries
        cur.fetchall.side_effect = [
            stock_prices,
            fx_rows,
            oil_rows,
            rate_rows,
        ]

        result = af.cross_asset_correlation("005930", "2024-05-21", window=20, db_conn=mock_db)
        assert "fx_corr_20d" in result
        assert "oil_corr_20d" in result
        assert "rate_corr_20d" in result

    def test_flat_stock_returns_zero(self, af, mock_db):
        cur = mock_db.cursor.return_value
        dates_20 = [date(2024, 5, i) for i in range(1, 22)]
        stock_prices = [(d, 100.0) for d in dates_20]
        stock_prices.reverse()
        cur.fetchall.return_value = stock_prices

        result = af.cross_asset_correlation("005930", "2024-05-21", db_conn=mock_db)
        assert result["fx_corr_20d"] == 0.0
        assert result["oil_corr_20d"] == 0.0
        assert result["rate_corr_20d"] == 0.0

    def test_insufficient_data(self, af, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [
            (date(2024, 6, 4), 100.0),
        ]
        result = af.cross_asset_correlation("005930", "2024-06-04", db_conn=mock_db)
        assert result["fx_corr_20d"] == 0.0

    def test_no_db_conn(self, af):
        result = af.cross_asset_correlation("005930", "2024-06-04")
        assert result["fx_corr_20d"] == 0.0
        assert result["oil_corr_20d"] == 0.0
        assert result["rate_corr_20d"] == 0.0

    def test_missing_macro_data(self, af, mock_db):
        cur = mock_db.cursor.return_value
        dates_20 = [date(2024, 5, i) for i in range(1, 22)]
        stock_prices = [(d, 100.0 + i * 0.5) for i, d in enumerate(dates_20)]
        stock_prices.reverse()

        cur.fetchall.side_effect = [
            stock_prices,
            [],  # fx - empty
            [],  # oil - empty
            [],  # rate - empty
        ]

        result = af.cross_asset_correlation("005930", "2024-05-21", db_conn=mock_db)
        assert result["fx_corr_20d"] == 0.0
        assert result["oil_corr_20d"] == 0.0
        assert result["rate_corr_20d"] == 0.0


class TestFlowStrength:
    def test_returns_z_scores(self, af, mock_db):
        cur = mock_db.cursor.return_value
        dates_25 = [date(2024, 5, d) for d in range(1, 26)]
        rows = [
            (d, 1000.0 + i * 10, 500.0 + i * 5)
            for i, d in enumerate(dates_25)
        ]
        rows.reverse()
        cur.fetchall.return_value = rows

        result = af.flow_strength("005930", "2024-05-25", db_conn=mock_db)
        assert "foreign_flow_z" in result
        assert "institution_flow_z" in result

    def test_insufficient_data(self, af, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [
            (date(2024, 6, 4), 1000.0, 500.0),
        ]
        result = af.flow_strength("005930", "2024-06-04", db_conn=mock_db)
        assert result["foreign_flow_z"] == 0.0
        assert result["institution_flow_z"] == 0.0

    def test_no_db_conn(self, af):
        result = af.flow_strength("005930", "2024-06-04")
        assert result["foreign_flow_z"] == 0.0
        assert result["institution_flow_z"] == 0.0

    def test_flat_flow_returns_zero(self, af, mock_db):
        cur = mock_db.cursor.return_value
        dates_25 = [date(2024, 5, d) for d in range(1, 26)]
        rows = [
            (d, 100.0, 50.0)
            for d in dates_25
        ]
        rows.reverse()
        cur.fetchall.return_value = rows

        result = af.flow_strength("005930", "2024-05-25", db_conn=mock_db)
        assert result["foreign_flow_z"] == 0.0
        assert result["institution_flow_z"] == 0.0


class TestShortSqueeze:
    def test_squeeze_detected(self, af, mock_db):
        cur = mock_db.cursor.return_value

        cur.fetchall.side_effect = [
            [
                (date(2024, 6, 4), 1.5),
                (date(2024, 6, 3), 2.0),
            ],
            [
                (date(2024, 6, 4), 110.0),
                (date(2024, 6, 3), 108.0),
                (date(2024, 6, 2), 106.0),
                (date(2024, 6, 1), 104.0),
                (date(2024, 5, 30), 100.0),
            ],
        ]

        result = af.short_squeeze("005930", "2024-06-04", db_conn=mock_db)
        assert result["short_squeeze"] == 1.0

    def test_no_squeeze_short_ratio_increased(self, af, mock_db):
        cur = mock_db.cursor.return_value

        cur.fetchall.side_effect = [
            [
                (date(2024, 6, 4), 2.5),
                (date(2024, 6, 3), 2.0),
            ],
            [
                (date(2024, 6, 4), 110.0),
                (date(2024, 6, 3), 108.0),
                (date(2024, 6, 2), 106.0),
                (date(2024, 6, 1), 104.0),
                (date(2024, 5, 30), 100.0),
            ],
        ]

        result = af.short_squeeze("005930", "2024-06-04", db_conn=mock_db)
        assert result["short_squeeze"] == 0.0

    def test_no_squeeze_low_return(self, af, mock_db):
        cur = mock_db.cursor.return_value

        cur.fetchall.side_effect = [
            [
                (date(2024, 6, 4), 1.5),
                (date(2024, 6, 3), 2.0),
            ],
            [
                (date(2024, 6, 4), 101.0),
                (date(2024, 6, 3), 100.5),
                (date(2024, 6, 2), 100.0),
                (date(2024, 6, 1), 99.5),
                (date(2024, 5, 30), 99.0),
            ],
        ]

        result = af.short_squeeze("005930", "2024-06-04", db_conn=mock_db)
        assert result["short_squeeze"] == 0.0

    def test_insufficient_short_data(self, af, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [
            (date(2024, 6, 4), 1.5),
        ]
        result = af.short_squeeze("005930", "2024-06-04", db_conn=mock_db)
        assert result["short_squeeze"] == 0.0

    def test_no_db_conn(self, af):
        result = af.short_squeeze("005930", "2024-06-04")
        assert result["short_squeeze"] == 0.0


class TestComputeAll:
    def test_returns_seven_features(self, af, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.side_effect = [
            # sentiment_surge
            [
                (date(2024, 6, 4), 0.5),
                (date(2024, 6, 3), 0.4),
                (date(2024, 6, 2), 0.45),
            ],
            # cross_asset_correlation - stock prices
            [(date(2024, 5, i), 100.0 + j * 0.5) for j, i in enumerate(range(1, 22))][::-1],
            # fx
            [(date(2024, 5, i), 1300.0) for i in range(1, 22)][::-1],
            # oil
            [(date(2024, 5, i), 70.0) for i in range(1, 22)][::-1],
            # rate
            [(date(2024, 5, i), 3.5) for i in range(1, 22)][::-1],
            # flow_strength
            [(date(2024, 5, d), 1000.0, 500.0) for d in range(1, 26)][::-1],
            # short_squeeze - short rows
            [
                (date(2024, 6, 4), 1.5),
                (date(2024, 6, 3), 2.0),
            ],
            # short_squeeze - price rows
            [
                (date(2024, 6, 4), 110.0),
                (date(2024, 6, 3), 108.0),
                (date(2024, 6, 2), 106.0),
                (date(2024, 6, 1), 104.0),
                (date(2024, 5, 30), 100.0),
            ],
        ]

        result = af.compute_all("005930", "2024-06-04", db_conn=mock_db)

        assert len(result) == 7
        assert "sentiment_surge" in result
        assert "fx_corr_20d" in result
        assert "oil_corr_20d" in result
        assert "rate_corr_20d" in result
        assert "foreign_flow_z" in result
        assert "institution_flow_z" in result
        assert "short_squeeze" in result

        assert result["short_squeeze"] == 1.0

    def test_no_db_conn(self, af):
        result = af.compute_all("005930", "2024-06-04")
        assert len(result) == 7
        assert all(v == 0.0 for v in result.values())
