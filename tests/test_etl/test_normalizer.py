import polars as pl
import pytest
import math

from services.shared.etl.normalizer import (
    ZScoreNormalizer,
    MinMaxNormalizer,
    RobustNormalizer,
    RankTransformer,
    Normalizer,
)


class TestZScoreNormalizer:
    def test_fit_transform_basic(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0], "b": [10.0, 20.0, 30.0, 40.0, 50.0]})
        n = ZScoreNormalizer()
        result = n.fit_transform(df, ["a", "b"])
        assert abs(result["a"].mean()) < 1e-10
        assert abs(result["a"].std(ddof=0) - 1.0) < 1e-10

    def test_inverse_transform(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        n = ZScoreNormalizer().fit(df, ["a"])
        transformed = n.transform(df)
        reconstructed = n.inverse_transform(transformed)
        assert abs(df["a"] - reconstructed["a"]).max() < 1e-10

    def test_zero_std(self):
        df = pl.DataFrame({"a": [5.0, 5.0, 5.0]})
        n = ZScoreNormalizer().fit(df, ["a"])
        result = n.transform(df)
        assert result["a"].to_list() == [0.0, 0.0, 0.0]

    def test_empty_df(self):
        n = ZScoreNormalizer()
        result = n.transform(pl.DataFrame({"a": pl.Series([], dtype=pl.Float64)}))
        assert result.is_empty()

    def test_missing_column_skipped(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        n = ZScoreNormalizer().fit(df, ["a", "missing"])
        result = n.transform(df)
        assert "a" in result.columns

    def test_null_handling(self):
        df = pl.DataFrame({"a": [1.0, None, 3.0]})
        n = ZScoreNormalizer().fit(df, ["a"])
        result = n.transform(df)
        assert result["a"].null_count() == 0

    def test_nan_handling(self):
        df = pl.DataFrame({"a": [1.0, float("nan"), 3.0]})
        n = ZScoreNormalizer().fit(df, ["a"])
        result = n.transform(df)
        assert result["a"].null_count() == 0
        assert not math.isnan(result["a"][1])

    def test_inverse_of_zero_std_returns_original_mean(self):
        df = pl.DataFrame({"a": [7.0, 7.0, 7.0]})
        n = ZScoreNormalizer().fit(df, ["a"])
        t = n.transform(df)
        reconstructed = n.inverse_transform(t)
        assert abs(reconstructed["a"] - 7.0).max() < 1e-10


class TestMinMaxNormalizer:
    def test_fit_transform_basic(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0]})
        n = MinMaxNormalizer()
        result = n.fit_transform(df, ["a"])
        assert result["a"][0] == 0.0
        assert result["a"][4] == 1.0

    def test_inverse_transform(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        n = MinMaxNormalizer().fit(df, ["a"])
        transformed = n.transform(df)
        reconstructed = n.inverse_transform(transformed)
        assert abs(df["a"] - reconstructed["a"]).max() < 1e-10

    def test_zero_range(self):
        df = pl.DataFrame({"a": [5.0, 5.0, 5.0]})
        n = MinMaxNormalizer().fit(df, ["a"])
        result = n.transform(df)
        assert result["a"].to_list() == [0.5, 0.5, 0.5]

    def test_empty_df(self):
        n = MinMaxNormalizer()
        result = n.transform(pl.DataFrame({"a": pl.Series([], dtype=pl.Float64)}))
        assert result.is_empty()

    def test_missing_column_skipped(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        n = MinMaxNormalizer().fit(df, ["missing"])
        result = n.transform(df)
        assert not result["a"].is_null().all()

    def test_null_handling(self):
        df = pl.DataFrame({"a": [1.0, None, 3.0]})
        n = MinMaxNormalizer().fit(df, ["a"])
        result = n.transform(df)
        assert result["a"].null_count() == 0

    def test_inverse_of_zero_range_returns_midpoint(self):
        df = pl.DataFrame({"a": [5.0, 5.0, 5.0]})
        n = MinMaxNormalizer().fit(df, ["a"])
        t = n.transform(df)
        reconstructed = n.inverse_transform(t)
        assert abs(reconstructed["a"] - 5.0).max() < 1e-10


class TestRobustNormalizer:
    def test_fit_transform_basic(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0]})
        n = RobustNormalizer()
        result = n.fit_transform(df, ["a"])
        assert abs(result["a"][2]) < 1e-10

    def test_inverse_transform(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        n = RobustNormalizer().fit(df, ["a"])
        transformed = n.transform(df)
        reconstructed = n.inverse_transform(transformed)
        assert abs(df["a"] - reconstructed["a"]).max() < 1e-10

    def test_zero_iqr(self):
        df = pl.DataFrame({"a": [5.0, 5.0, 5.0]})
        n = RobustNormalizer().fit(df, ["a"])
        result = n.transform(df)
        assert result["a"].to_list() == [0.0, 0.0, 0.0]

    def test_empty_df(self):
        n = RobustNormalizer()
        result = n.transform(pl.DataFrame({"a": pl.Series([], dtype=pl.Float64)}))
        assert result.is_empty()

    def test_missing_column_skipped(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        n = RobustNormalizer().fit(df, ["missing"])
        result = n.transform(df)
        assert "a" in result.columns

    def test_null_handling(self):
        df = pl.DataFrame({"a": [1.0, None, 3.0]})
        n = RobustNormalizer().fit(df, ["a"])
        result = n.transform(df)
        assert result["a"].null_count() == 0

    def test_inverse_of_zero_iqr_returns_original_median(self):
        df = pl.DataFrame({"a": [7.0, 7.0, 7.0]})
        n = RobustNormalizer().fit(df, ["a"])
        t = n.transform(df)
        reconstructed = n.inverse_transform(t)
        assert abs(reconstructed["a"] - 7.0).max() < 1e-10


class TestRankTransformer:
    def test_transform_basic(self):
        df = pl.DataFrame({"a": [1.0, 3.0, 2.0, 5.0, 4.0]})
        t = RankTransformer()
        result = t.transform(df, ["a"])
        expected = [0.0, 0.5, 0.25, 1.0, 0.75]
        assert all(abs(r - e) < 1e-10 for r, e in zip(result["a"].to_list(), expected))

    def test_single_row(self):
        df = pl.DataFrame({"a": [42.0]})
        t = RankTransformer()
        result = t.transform(df, ["a"])
        assert result["a"][0] == 0.5

    def test_two_rows(self):
        df = pl.DataFrame({"a": [10.0, 20.0]})
        t = RankTransformer()
        result = t.transform(df, ["a"])
        assert result["a"].to_list() == [0.0, 1.0]

    def test_empty_df(self):
        t = RankTransformer()
        result = t.transform(pl.DataFrame({"a": pl.Series([], dtype=pl.Float64)}), ["a"])
        assert result.is_empty()

    def test_missing_column_skipped(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        t = RankTransformer()
        result = t.transform(df, ["missing"])
        assert "a" in result.columns

    def test_null_handling(self):
        df = pl.DataFrame({"a": [1.0, None, 3.0]})
        t = RankTransformer()
        result = t.transform(df, ["a"])
        assert result["a"].null_count() == 0

    def test_ties_average(self):
        df = pl.DataFrame({"a": [1.0, 1.0, 2.0, 2.0, 3.0]})
        t = RankTransformer()
        result = t.transform(df, ["a"])
        vals = result["a"].to_list()
        assert vals[0] == vals[1]
        assert vals[2] == vals[3]


class TestNormalizer:
    def test_zscore_dispatch(self):
        n = Normalizer(method="zscore")
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        result = n.fit_transform(df, ["a"])
        assert abs(result["a"].std(ddof=0) - 1.0) < 1e-10

    def test_minmax_dispatch(self):
        n = Normalizer(method="minmax")
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        result = n.fit_transform(df, ["a"])
        assert result["a"][0] == 0.0
        assert result["a"][2] == 1.0

    def test_robust_dispatch(self):
        n = Normalizer(method="robust")
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        result = n.fit_transform(df, ["a"])
        assert abs(result["a"][1]) < 1e-10

    def test_rank_dispatch(self):
        n = Normalizer(method="rank")
        df = pl.DataFrame({"a": [3.0, 1.0, 2.0]})
        result = n.fit_transform(df, ["a"])
        assert result["a"][0] == 1.0

    def test_inverse_transform_zscore(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        n = Normalizer(method="zscore").fit(df, ["a"])
        t = n.transform(df)
        reconstructed = n.inverse_transform(t)
        assert abs(df["a"] - reconstructed["a"]).max() < 1e-10

    def test_inverse_transform_minmax(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        n = Normalizer(method="minmax").fit(df, ["a"])
        t = n.transform(df)
        reconstructed = n.inverse_transform(t)
        assert abs(df["a"] - reconstructed["a"]).max() < 1e-10

    def test_inverse_transform_robust(self):
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        n = Normalizer(method="robust").fit(df, ["a"])
        t = n.transform(df)
        reconstructed = n.inverse_transform(t)
        assert abs(df["a"] - reconstructed["a"]).max() < 1e-10

    def test_rank_inverse_is_noop(self):
        n = Normalizer(method="rank")
        df = pl.DataFrame({"a": [1.0, 2.0, 3.0]})
        result = n.fit_transform(df, ["a"])
        inverse = n.inverse_transform(result)
        assert abs(result["a"] - inverse["a"]).max() < 1e-10

    def test_invalid_method(self):
        with pytest.raises(ValueError, match="Unknown method"):
            Normalizer(method="unknown")

    def test_method_preserved(self):
        for m in ("zscore", "minmax", "robust", "rank"):
            n = Normalizer(method=m)
            assert n is not None

    def test_empty_df(self):
        for m in ("zscore", "minmax", "robust", "rank"):
            n = Normalizer(method=m)
            df = pl.DataFrame({"a": pl.Series([], dtype=pl.Float64)})
            result = n.fit_transform(df, ["a"])
            assert result.is_empty()
