import pytest
import polars as pl
import math
from services.shared.etl.data_cleaner import (
    OutlierDetector,
    MissingValueHandler,
    DuplicateRemover,
    DataQualityScorer,
    DataCleaner,
)






# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def outlier_df():
    return pl.DataFrame({
        "value": [1.0, 2.0, 3.0, 4.0, 5.0, 100.0],
        "group": ["a", "a", "a", "b", "b", "b"],
    })


@pytest.fixture
def null_df():
    return pl.DataFrame({
        "value": [1.0, None, 3.0, None, 5.0],
        "group": ["a", "a", "b", "b", "c"],
    })


@pytest.fixture
def dup_df():
    return pl.DataFrame({
        "id": [1, 1, 2, 2, 3],
        "val": ["a", "a", "b", "c", "d"],
    })


@pytest.fixture
def empty_df():
    return pl.DataFrame({"a": []})


# ---------------------------------------------------------------------------
# OutlierDetector
# ---------------------------------------------------------------------------

class TestOutlierDetector:
    def test_iqr_clips_extreme_values(self, outlier_df):
        detector = OutlierDetector()
        result = detector.iqr(outlier_df, ["value"])
        # Q1=2, Q3=5, IQR=3 → upper=5+9=14, clips 100 → 14
        assert result["value"].to_list()[-1] == 14.0

    def test_iqr_preserves_normal_values(self):
        df = pl.DataFrame({"x": [10, 20, 30, 40, 50]})
        detector = OutlierDetector()
        result = detector.iqr(df, ["x"])
        assert result["x"].to_list() == [10, 20, 30, 40, 50]

    def test_iqr_custom_multiplier(self):
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]})
        detector = OutlierDetector()
        result = detector.iqr(df, ["x"], multiplier=1.5)
        # Q1=2, Q3=5, IQR=3 → upper=5+4.5=9.5, clips 100 → 9.5
        assert result["x"].to_list()[-1] == 9.5

    def test_iqr_empty_df(self, empty_df):
        with pytest.warns(UserWarning, match="Empty DataFrame"):
            detector = OutlierDetector()
            result = detector.iqr(empty_df, ["a"])
            assert result.shape == empty_df.shape

    def test_iqr_missing_column_warns(self):
        df = pl.DataFrame({"a": [1, 2, 3]})
        with pytest.warns(UserWarning, match="not found"):
            detector = OutlierDetector()
            result = detector.iqr(df, ["a", "bogus"])
            assert "a" in result.columns

    def test_iqr_all_null_column_warns(self):
        df = pl.DataFrame({"x": [None, None, None]})
        with pytest.warns(UserWarning, match="all-null"):
            detector = OutlierDetector()
            result = detector.iqr(df, ["x"])
            assert result["x"].to_list() == [None, None, None]

    def test_iqr_non_numeric_column_warns(self):
        df = pl.DataFrame({"x": ["a", "b", "c"]})
        with pytest.warns(UserWarning, match="not numeric"):
            detector = OutlierDetector()
            result = detector.iqr(df, ["x"])
            assert result["x"].to_list() == ["a", "b", "c"]

    def test_zscore_replaces_outliers(self):
        vals = [1.0] * 20 + [100.0]
        df = pl.DataFrame({"x": vals})
        detector = OutlierDetector()
        result = detector.zscore(df, ["x"])
        assert result["x"].to_list()[-1] == 1.0

    def test_zscore_threshold(self):
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]})
        detector = OutlierDetector()
        result = detector.zscore(df, ["x"], threshold=1.0)
        assert result["x"].to_list()[-1] == 3.5

    def test_zscore_constant_column_skips(self):
        df = pl.DataFrame({"x": [5.0, 5.0, 5.0]})
        with pytest.warns(UserWarning, match="zero standard deviation"):
            detector = OutlierDetector()
            result = detector.zscore(df, ["x"])
            assert result["x"].to_list() == [5.0, 5.0, 5.0]

    def test_zscore_empty_df(self, empty_df):
        with pytest.warns(UserWarning, match="Empty DataFrame"):
            detector = OutlierDetector()
            result = detector.zscore(empty_df, ["a"])
            assert result.shape == empty_df.shape

    def test_mad_replaces_outliers(self):
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 100.0]})
        detector = OutlierDetector()
        result = detector.mad(df, ["x"])
        median = 3.0
        assert result["x"].to_list() == [1.0, 2.0, 3.0, 4.0, median]

    def test_mad_empty_df(self, empty_df):
        with pytest.warns(UserWarning, match="Empty DataFrame"):
            detector = OutlierDetector()
            result = detector.mad(empty_df, ["a"])
            assert result.shape == empty_df.shape

    def test_mad_zero_mad_skips(self):
        df = pl.DataFrame({"x": [5.0, 5.0, 5.0]})
        with pytest.warns(UserWarning, match="zero MAD"):
            detector = OutlierDetector()
            result = detector.mad(df, ["x"])
            assert result["x"].to_list() == [5.0, 5.0, 5.0]

    def test_mad_all_null_skips(self):
        df = pl.DataFrame({"x": [None, None, None]})
        with pytest.warns(UserWarning, match="all-null"):
            detector = OutlierDetector()
            result = detector.mad(df, ["x"])
            assert result["x"].to_list() == [None, None, None]


# ---------------------------------------------------------------------------
# MissingValueHandler
# ---------------------------------------------------------------------------

class TestMissingValueHandler:
    def test_ffill_basic(self, null_df):
        handler = MissingValueHandler()
        result = handler.ffill(null_df, ["value"])
        assert result["value"].to_list() == [1.0, 1.0, 3.0, 3.0, 5.0]

    def test_ffill_with_group_by(self):
        df = pl.DataFrame({
            "g": ["a", "a", "a", "b", "b", "b"],
            "v": [1.0, None, 3.0, 4.0, None, 6.0],
        })
        handler = MissingValueHandler()
        result = handler.ffill(df, ["v"], group_by="g")
        assert result["v"].to_list() == [1.0, 1.0, 3.0, 4.0, 4.0, 6.0]

    def test_bfill_basic(self, null_df):
        handler = MissingValueHandler()
        result = handler.bfill(null_df, ["value"])
        assert result["value"].to_list() == [1.0, 3.0, 3.0, 5.0, 5.0]

    def test_bfill_with_group_by(self):
        df = pl.DataFrame({
            "g": ["a", "a", "a", "b", "b", "b"],
            "v": [1.0, None, 3.0, 4.0, None, 6.0],
        })
        handler = MissingValueHandler()
        result = handler.bfill(df, ["v"], group_by="g")
        assert result["v"].to_list() == [1.0, 3.0, 3.0, 4.0, 6.0, 6.0]

    def test_linear_interpolate(self):
        df = pl.DataFrame({"x": [1.0, None, 3.0]})
        handler = MissingValueHandler()
        result = handler.linear_interpolate(df, ["x"])
        assert result["x"].to_list() == [1.0, 2.0, 3.0]

    def test_linear_interpolate_with_group_by(self):
        df = pl.DataFrame({
            "g": ["a", "a", "a", "b", "b", "b"],
            "v": [1.0, None, 3.0, 10.0, None, 30.0],
        })
        handler = MissingValueHandler()
        result = handler.linear_interpolate(df, ["v"], group_by="g")
        assert result["v"].to_list() == [1.0, 2.0, 3.0, 10.0, 20.0, 30.0]

    def test_fill_constant(self, null_df):
        handler = MissingValueHandler()
        result = handler.fill_constant(null_df, ["value"], value=-1.0)
        assert result["value"].to_list() == [1.0, -1.0, 3.0, -1.0, 5.0]

    def test_fill_constant_default(self, null_df):
        handler = MissingValueHandler()
        result = handler.fill_constant(null_df, ["value"])
        assert result["value"].to_list() == [1.0, 0.0, 3.0, 0.0, 5.0]

    def test_auto_fill_ffill(self, null_df):
        handler = MissingValueHandler()
        result = handler.auto_fill(null_df, ["value"], strategy="ffill")
        assert result["value"].to_list() == [1.0, 1.0, 3.0, 3.0, 5.0]

    def test_auto_fill_bfill(self, null_df):
        handler = MissingValueHandler()
        result = handler.auto_fill(null_df, ["value"], strategy="bfill")
        assert result["value"].to_list() == [1.0, 3.0, 3.0, 5.0, 5.0]

    def test_auto_fill_linear(self):
        df = pl.DataFrame({"x": [1.0, None, 3.0]})
        handler = MissingValueHandler()
        result = handler.auto_fill(df, ["x"], strategy="linear")
        assert result["x"].to_list() == [1.0, 2.0, 3.0]

    def test_auto_fill_constant(self, null_df):
        handler = MissingValueHandler()
        result = handler.auto_fill(null_df, ["value"], strategy="constant")
        assert result["value"].to_list() == [1.0, 0.0, 3.0, 0.0, 5.0]

    def test_auto_fill_unknown_strategy_falls_back(self, null_df):
        with pytest.warns(UserWarning, match="Unknown strategy"):
            handler = MissingValueHandler()
            result = handler.auto_fill(null_df, ["value"], strategy="bogus")
            assert result["value"].to_list() == [1.0, 1.0, 3.0, 3.0, 5.0]

    def test_empty_df(self, empty_df):
        handler = MissingValueHandler()
        result = handler.ffill(empty_df, ["a"])
        assert result.shape == empty_df.shape

    def test_missing_column_warns(self):
        df = pl.DataFrame({"a": [1, None]})
        with pytest.warns(UserWarning, match="not found"):
            handler = MissingValueHandler()
            result = handler.ffill(df, ["a", "bogus"])
            assert result["a"].to_list() == [1, 1]


# ---------------------------------------------------------------------------
# DuplicateRemover
# ---------------------------------------------------------------------------

class TestDuplicateRemover:
    def test_remove_exact_duplicates(self, dup_df):
        remover = DuplicateRemover()
        result = remover.remove_exact_duplicates(dup_df)
        assert result.shape[0] == 4
        assert set(result["id"].to_list()) == {1, 2, 3}

    def test_remove_exact_duplicates_subset(self, dup_df):
        remover = DuplicateRemover()
        result = remover.remove_exact_duplicates(dup_df, subset=["id"])
        assert result.shape[0] == 3
        assert set(result["id"].to_list()) == {1, 2, 3}

    def test_remove_partial_duplicates_keep_last(self):
        df = pl.DataFrame({
            "key": ["a", "a", "b"],
            "val": [1, 2, 3],
        })
        remover = DuplicateRemover()
        result = remover.remove_partial_duplicates(df, key_columns=["key"], keep="last")
        assert result.shape[0] == 2
        assert result["val"].to_list() == [2, 3]

    def test_remove_partial_duplicates_keep_first(self):
        df = pl.DataFrame({
            "key": ["a", "a", "b"],
            "val": [1, 2, 3],
        })
        remover = DuplicateRemover()
        result = remover.remove_partial_duplicates(df, key_columns=["key"], keep="first")
        assert result.shape[0] == 2
        assert result["val"].to_list() == [1, 3]

    def test_empty_df(self, empty_df):
        remover = DuplicateRemover()
        result = remover.remove_exact_duplicates(empty_df)
        assert result.shape == empty_df.shape

    def test_no_key_columns_warns(self):
        df = pl.DataFrame({"a": [1, 2]})
        with pytest.warns(UserWarning, match="No key columns"):
            remover = DuplicateRemover()
            result = remover.remove_partial_duplicates(df, [])
            assert result.shape[0] == 2

    def test_missing_key_columns_warns(self):
        df = pl.DataFrame({"a": [1, 2]})
        with pytest.warns(UserWarning, match="not found"):
            remover = DuplicateRemover()
            result = remover.remove_partial_duplicates(df, ["bogus"])
            assert result.shape[0] == 2

    def test_subset_missing_columns_warns(self):
        df = pl.DataFrame({"a": [1, 1, 2]})
        with pytest.warns(UserWarning, match="not found"):
            remover = DuplicateRemover()
            result = remover.remove_exact_duplicates(df, subset=["a", "bogus"])
            assert result.shape[0] == 2


# ---------------------------------------------------------------------------
# DataQualityScorer
# ---------------------------------------------------------------------------

class TestDataQualityScorer:
    def test_score_keys(self):
        df = pl.DataFrame({"a": [1, 2, 3]})
        scorer = DataQualityScorer()
        report = scorer.score(df)
        assert set(report.keys()) == {"completeness", "consistency", "timeliness", "composite"}

    def test_completeness_perfect(self):
        df = pl.DataFrame({"a": [1, 2, 3]})
        scorer = DataQualityScorer()
        report = scorer.score(df)
        assert report["completeness"] == 1.0

    def test_completeness_half(self):
        df = pl.DataFrame({"a": [1.0, None]})
        scorer = DataQualityScorer()
        report = scorer.score(df)
        assert report["completeness"] == 0.5

    def test_timeliness_without_date_returns_one(self):
        df = pl.DataFrame({"a": [1, 2, 3]})
        scorer = DataQualityScorer()
        report = scorer.score(df)
        assert report["timeliness"] == 1.0

    def test_timeliness_with_recent_date(self):
        import datetime
        df = pl.DataFrame({
            "date": [datetime.date.today(), datetime.date.today()],
            "val": [1.0, 2.0],
        })
        scorer = DataQualityScorer()
        report = scorer.score(df)
        assert report["timeliness"] == 1.0

    def test_empty_df_scores_zero(self, empty_df):
        scorer = DataQualityScorer()
        report = scorer.score(empty_df)
        assert report == {"completeness": 0.0, "consistency": 0.0, "timeliness": 0.0, "composite": 0.0}

    def test_composite_sum(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        scorer = DataQualityScorer()
        report = scorer.score(df)
        expected = report["completeness"] + report["consistency"] + report["timeliness"]
        assert report["composite"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# DataCleaner
# ---------------------------------------------------------------------------

class TestDataCleaner:
    def test_clean_returns_dataframe(self, outlier_df):
        cleaner = DataCleaner()
        result = cleaner.clean(outlier_df)
        assert isinstance(result, pl.DataFrame)

    def test_clean_pipeline_order(self):
        df = pl.DataFrame({
            "id": [1, 1, 2],
            "val": [10.0, None, 1000.0],
        })
        cleaner = DataCleaner(outlier_method="iqr", fill_method="ffill", remove_duplicates=True)
        result = cleaner.clean(df)
        assert result.shape[0] <= df.shape[0]
        assert result["val"].null_count() == 0

    def test_clean_without_dedup(self):
        df = pl.DataFrame({
            "id": [1, 1],
            "val": [10.0, 10.0],
        })
        cleaner = DataCleaner(remove_duplicates=False)
        result = cleaner.clean(df)
        assert result.shape[0] == 2

    def test_clean_with_zscore_method(self):
        df = pl.DataFrame({"val": [10.0, 20.0, 30.0, 1000.0]})
        cleaner = DataCleaner(outlier_method="zscore", remove_duplicates=False)
        result = cleaner.clean(df)
        assert result["val"].null_count() == 0

    def test_clean_with_mad_method(self):
        df = pl.DataFrame({"val": [1.0, 2.0, 3.0, 4.0, 100.0]})
        cleaner = DataCleaner(outlier_method="mad", remove_duplicates=False)
        result = cleaner.clean(df)
        assert result["val"].null_count() == 0

    def test_clean_with_report_returns_tuple(self, outlier_df):
        cleaner = DataCleaner()
        cleaned, report = cleaner.clean_with_report(outlier_df)
        assert isinstance(cleaned, pl.DataFrame)
        assert isinstance(report, dict)
        assert "completeness" in report

    def test_clean_empty_df(self, empty_df):
        cleaner = DataCleaner()
        result = cleaner.clean(empty_df)
        assert result.shape == empty_df.shape

    def test_clean_with_report_empty_df(self, empty_df):
        cleaner = DataCleaner()
        cleaned, report = cleaner.clean_with_report(empty_df)
        assert cleaned.shape == empty_df.shape
        assert report["completeness"] == 0.0
