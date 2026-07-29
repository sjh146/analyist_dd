import logging
import polars as pl

logger = logging.getLogger(__name__)


def _clean_series(series: pl.Series) -> pl.Series:
    return series.cast(pl.Float64).fill_nan(None).drop_nulls()


class ZScoreNormalizer:
    def __init__(self):
        self._mean: dict[str, float] = {}
        self._std: dict[str, float] = {}

    def fit(self, df: pl.DataFrame, columns: list) -> "ZScoreNormalizer":
        if df.is_empty() or not columns:
            return self
        for col in columns:
            if col not in df.columns:
                logger.warning("Column '%s' not found, skipping", col)
                continue
            series = _clean_series(df[col])
            if len(series) == 0:
                logger.warning("Column '%s' has no non-null values, skipping", col)
                continue
            self._mean[col] = series.mean()
            self._std[col] = series.std(ddof=0)
        return self

    def transform(self, df: pl.DataFrame) -> pl.DataFrame:
        if df.is_empty():
            return df
        result = df.clone()
        for col, mean in self._mean.items():
            if col not in df.columns:
                continue
            std = self._std[col]
            if std == 0:
                result = result.with_columns(pl.lit(0.0, dtype=pl.Float64).alias(col))
            else:
                expr = ((df[col].cast(pl.Float64) - mean) / std).fill_nan(0).fill_null(0)
                result = result.with_columns(expr.alias(col))
        return result

    def fit_transform(self, df: pl.DataFrame, columns: list) -> pl.DataFrame:
        self.fit(df, columns)
        return self.transform(df)

    def inverse_transform(self, df: pl.DataFrame) -> pl.DataFrame:
        if df.is_empty():
            return df
        result = df.clone()
        for col, mean in self._mean.items():
            if col not in df.columns:
                continue
            std = self._std[col]
            expr = (df[col].cast(pl.Float64) * std + mean).fill_nan(0).fill_null(0)
            result = result.with_columns(expr.alias(col))
        return result


class MinMaxNormalizer:
    def __init__(self):
        self._min: dict[str, float] = {}
        self._max: dict[str, float] = {}

    def fit(self, df: pl.DataFrame, columns: list) -> "MinMaxNormalizer":
        if df.is_empty() or not columns:
            return self
        for col in columns:
            if col not in df.columns:
                logger.warning("Column '%s' not found, skipping", col)
                continue
            series = _clean_series(df[col])
            if len(series) == 0:
                logger.warning("Column '%s' has no non-null values, skipping", col)
                continue
            self._min[col] = series.min()
            self._max[col] = series.max()
        return self

    def transform(self, df: pl.DataFrame) -> pl.DataFrame:
        if df.is_empty():
            return df
        result = df.clone()
        for col, mn in self._min.items():
            if col not in df.columns:
                continue
            mx = self._max[col]
            rng = mx - mn
            if rng == 0:
                result = result.with_columns(pl.lit(0.5, dtype=pl.Float64).alias(col))
            else:
                expr = ((df[col].cast(pl.Float64) - mn) / rng).fill_nan(0).fill_null(0)
                result = result.with_columns(expr.alias(col))
        return result

    def fit_transform(self, df: pl.DataFrame, columns: list) -> pl.DataFrame:
        self.fit(df, columns)
        return self.transform(df)

    def inverse_transform(self, df: pl.DataFrame) -> pl.DataFrame:
        if df.is_empty():
            return df
        result = df.clone()
        for col, mn in self._min.items():
            if col not in df.columns:
                continue
            mx = self._max[col]
            rng = mx - mn
            expr = (df[col].cast(pl.Float64) * rng + mn).fill_nan(0).fill_null(0)
            result = result.with_columns(expr.alias(col))
        return result


class RobustNormalizer:
    def __init__(self):
        self._median: dict[str, float] = {}
        self._q1: dict[str, float] = {}
        self._q3: dict[str, float] = {}

    def fit(self, df: pl.DataFrame, columns: list) -> "RobustNormalizer":
        if df.is_empty() or not columns:
            return self
        for col in columns:
            if col not in df.columns:
                logger.warning("Column '%s' not found, skipping", col)
                continue
            series = _clean_series(df[col])
            if len(series) == 0:
                logger.warning("Column '%s' has no non-null values, skipping", col)
                continue
            self._median[col] = series.median()
            self._q1[col] = series.quantile(0.25)
            self._q3[col] = series.quantile(0.75)
        return self

    def transform(self, df: pl.DataFrame) -> pl.DataFrame:
        if df.is_empty():
            return df
        result = df.clone()
        for col, med in self._median.items():
            if col not in df.columns:
                continue
            q1 = self._q1[col]
            q3 = self._q3[col]
            iqr = q3 - q1
            if iqr == 0:
                result = result.with_columns(pl.lit(0.0, dtype=pl.Float64).alias(col))
            else:
                expr = ((df[col].cast(pl.Float64) - med) / iqr).fill_nan(0).fill_null(0)
                result = result.with_columns(expr.alias(col))
        return result

    def fit_transform(self, df: pl.DataFrame, columns: list) -> pl.DataFrame:
        self.fit(df, columns)
        return self.transform(df)

    def inverse_transform(self, df: pl.DataFrame) -> pl.DataFrame:
        if df.is_empty():
            return df
        result = df.clone()
        for col, med in self._median.items():
            if col not in df.columns:
                continue
            q1 = self._q1[col]
            q3 = self._q3[col]
            iqr = q3 - q1
            expr = (df[col].cast(pl.Float64) * iqr + med).fill_nan(0).fill_null(0)
            result = result.with_columns(expr.alias(col))
        return result


class RankTransformer:
    def transform(
        self, df: pl.DataFrame, columns: list, method: str = "average"
    ) -> pl.DataFrame:
        if df.is_empty() or not columns:
            return df
        result = df.clone()
        n = len(df)
        for col in columns:
            if col not in df.columns:
                logger.warning("Column '%s' not found, skipping", col)
                continue
            clean = df[col].cast(pl.Float64).fill_nan(0).fill_null(0)
            rank_col = clean.rank(method=method)
            if n <= 1:
                result = result.with_columns(pl.lit(0.5, dtype=pl.Float64).alias(col))
            else:
                result = result.with_columns(
                    ((rank_col - 1).cast(pl.Float64) / (n - 1)).alias(col)
                )
        return result


class Normalizer:
    def __init__(self, method: str = "zscore"):
        if method not in ("zscore", "minmax", "robust", "rank"):
            raise ValueError(
                f"Unknown method '{method}'. Use 'zscore', 'minmax', 'robust', or 'rank'."
            )
        self._method = method
        self._columns: list = []
        if method == "zscore":
            self._inner = ZScoreNormalizer()
        elif method == "minmax":
            self._inner = MinMaxNormalizer()
        elif method == "robust":
            self._inner = RobustNormalizer()
        elif method == "rank":
            self._inner = RankTransformer()

    def fit(self, df: pl.DataFrame, columns: list) -> "Normalizer":
        self._columns = columns
        if hasattr(self._inner, "fit"):
            self._inner.fit(df, columns)
        return self

    def transform(self, df: pl.DataFrame) -> pl.DataFrame:
        if self._method == "rank":
            return self._inner.transform(df, self._columns)
        return self._inner.transform(df)

    def fit_transform(self, df: pl.DataFrame, columns: list) -> pl.DataFrame:
        self.fit(df, columns)
        return self.transform(df)

    def inverse_transform(self, df: pl.DataFrame) -> pl.DataFrame:
        if hasattr(self._inner, "inverse_transform"):
            return self._inner.inverse_transform(df)
        return df
