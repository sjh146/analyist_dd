"""Unit tests for factor_base cross-sectional helpers."""

import pytest

from app.factors.factor_base import (
    normalize_rank_confidence,
    rank_scores,
    zscore_scores,
)


class TestRankScores:
    def test_ascending_rank_lowest_best(self):
        scores = {"A": 10.0, "B": 5.0, "C": 1.0}
        ranks = rank_scores(scores, ascending=True)
        assert ranks == {"C": 1, "B": 2, "A": 3}

    def test_descending_rank_highest_best(self):
        scores = {"A": 0.05, "B": 0.12, "C": 0.30}
        ranks = rank_scores(scores, ascending=False)
        assert ranks == {"C": 1, "B": 2, "A": 3}

    def test_none_values_excluded(self):
        scores = {"A": 10.0, "B": None, "C": 1.0}
        ranks = rank_scores(scores, ascending=True)
        assert ranks == {"C": 1, "A": 2}
        assert "B" not in ranks

    def test_empty_scores(self):
        assert rank_scores({}) == {}

    def test_all_none(self):
        assert rank_scores({"A": None, "B": None}) == {}


class TestZscoreScores:
    def test_mean_zero_std_one(self):
        scores = {"A": 1.0, "B": 2.0, "C": 3.0}
        z = zscore_scores(scores, higher_is_better=True)
        assert z["B"] == pytest.approx(0.0, abs=1e-9)
        assert z["C"] > 0
        assert z["A"] < 0

    def test_higher_is_better_flips_sign(self):
        scores = {"A": 1.0, "B": 2.0, "C": 3.0}
        z_high = zscore_scores(scores, higher_is_better=True)
        z_low = zscore_scores(scores, higher_is_better=False)
        assert z_low["C"] == pytest.approx(-z_high["C"], abs=1e-9)
        assert z_low["A"] == pytest.approx(-z_high["A"], abs=1e-9)

    def test_degenerate_zero_variance_returns_empty(self):
        scores = {"A": 5.0, "B": 5.0, "C": 5.0}
        assert zscore_scores(scores) == {}

    def test_single_value_returns_empty(self):
        assert zscore_scores({"A": 1.0}) == {}

    def test_none_values_excluded(self):
        scores = {"A": 1.0, "B": 3.0, "C": None}
        z = zscore_scores(scores, higher_is_better=True)
        assert "C" not in z
        assert z["A"] < 0 < z["B"]


class TestNormalizeRankConfidence:
    def test_top_rank_high_confidence(self):
        assert normalize_rank_confidence(1, 30) == pytest.approx(0.95)

    def test_bottom_rank_low_confidence(self):
        confidence = normalize_rank_confidence(30, 30)
        assert 0.5 <= confidence < 0.6
        assert confidence == pytest.approx(0.515, abs=1e-3)

    def test_mid_rank(self):
        assert 0.5 < normalize_rank_confidence(15, 30) < 0.95

    def test_zero_total(self):
        assert normalize_rank_confidence(1, 0) == 0.5
