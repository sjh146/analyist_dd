import polars as pl
import warnings
import datetime


class OutlierDetector:
    def iqr(self, df: pl.DataFrame, columns: list, multiplier: float = 3.0) -> pl.DataFrame:
        if df.is_empty():
            warnings.warn("Empty DataFrame passed to IQR outlier detection")
            return df
        existing = _resolve_columns(df, columns, "IQR")
        if not existing:
            return df
        result = df
        for col_name in existing:
            col = df[col_name]
            if col.is_null().all():
                warnings.warn(f"Column '{col_name}' is all-null, skipping IQR")
                continue
            if not col.dtype.is_numeric():
                warnings.warn(f"Column '{col_name}' is not numeric, skipping IQR")
                continue
            q1 = col.quantile(0.25)
            q3 = col.quantile(0.75)
            iqr_val = q3 - q1
            lower = q1 - multiplier * iqr_val
            upper = q3 + multiplier * iqr_val
            result = result.with_columns(
                pl.when(pl.col(col_name) < lower).then(lower)
                .when(pl.col(col_name) > upper).then(upper)
                .otherwise(pl.col(col_name)).alias(col_name)
            )
        return result

    def zscore(self, df: pl.DataFrame, columns: list, threshold: float = 3.0) -> pl.DataFrame:
        if df.is_empty():
            warnings.warn("Empty DataFrame passed to Z-score outlier detection")
            return df
        existing = _resolve_columns(df, columns, "Z-score")
        if not existing:
            return df
        result = df
        for col_name in existing:
            col = df[col_name]
            if col.is_null().all():
                warnings.warn(f"Column '{col_name}' is all-null, skipping Z-score")
                continue
            if not col.dtype.is_numeric():
                warnings.warn(f"Column '{col_name}' is not numeric, skipping Z-score")
                continue
            mean = col.mean()
            std = col.std()
            if std is None or std == 0:
                warnings.warn(f"Column '{col_name}' has zero standard deviation, skipping Z-score")
                continue
            median = col.median()
            result = result.with_columns(
                pl.when(
                    ((pl.col(col_name) - mean) / std).abs() > threshold
                ).then(median)
                .otherwise(pl.col(col_name)).alias(col_name)
            )
        return result

    def mad(self, df: pl.DataFrame, columns: list, threshold: float = 3.5) -> pl.DataFrame:
        if df.is_empty():
            warnings.warn("Empty DataFrame passed to MAD outlier detection")
            return df
        existing = _resolve_columns(df, columns, "MAD")
        if not existing:
            return df
        result = df
        for col_name in existing:
            col = df[col_name]
            if col.is_null().all():
                warnings.warn(f"Column '{col_name}' is all-null, skipping MAD")
                continue
            if not col.dtype.is_numeric():
                warnings.warn(f"Column '{col_name}' is not numeric, skipping MAD")
                continue
            median = col.median()
            abs_dev = (col - median).abs()
            mad_val = abs_dev.median()
            if mad_val is None or mad_val == 0:
                warnings.warn(f"Column '{col_name}' has zero MAD, skipping")
                continue
            result = result.with_columns(
                pl.when(
                    (0.6745 * (pl.col(col_name) - median).abs() / mad_val) > threshold
                ).then(median)
                .otherwise(pl.col(col_name)).alias(col_name)
            )
        return result


class MissingValueHandler:
    def ffill(self, df: pl.DataFrame, columns: list, group_by: str = None) -> pl.DataFrame:
        if df.is_empty():
            return df
        existing = _resolve_columns(df, columns, "ffill")
        if not existing:
            return df
        result = df
        for col_name in existing:
            expr = pl.col(col_name).fill_null(strategy="forward")
            if group_by is not None and group_by in df.columns:
                expr = expr.over(group_by)
            result = result.with_columns(expr)
        return result

    def bfill(self, df: pl.DataFrame, columns: list, group_by: str = None) -> pl.DataFrame:
        if df.is_empty():
            return df
        existing = _resolve_columns(df, columns, "bfill")
        if not existing:
            return df
        result = df
        for col_name in existing:
            expr = pl.col(col_name).fill_null(strategy="backward")
            if group_by is not None and group_by in df.columns:
                expr = expr.over(group_by)
            result = result.with_columns(expr)
        return result

    def linear_interpolate(self, df: pl.DataFrame, columns: list, group_by: str = None) -> pl.DataFrame:
        if df.is_empty():
            return df
        existing = _resolve_columns(df, columns, "linear_interpolate")
        if not existing:
            return df
        result = df
        for col_name in existing:
            expr = pl.col(col_name).interpolate()
            if group_by is not None and group_by in df.columns:
                expr = expr.over(group_by)
            result = result.with_columns(expr)
        return result

    def fill_constant(self, df: pl.DataFrame, columns: list, value: float = 0.0) -> pl.DataFrame:
        if df.is_empty():
            return df
        existing = _resolve_columns(df, columns, "fill_constant")
        if not existing:
            return df
        result = df
        for col_name in existing:
            result = result.with_columns(
                pl.col(col_name).fill_null(value)
            )
        return result

    def auto_fill(self, df: pl.DataFrame, columns: list, strategy: str = 'ffill') -> pl.DataFrame:
        strategy_map = {
            'ffill': self.ffill,
            'bfill': self.bfill,
            'linear': self.linear_interpolate,
            'constant': self.fill_constant,
        }
        if strategy not in strategy_map:
            warnings.warn(f"Unknown strategy '{strategy}', falling back to 'ffill'")
            return self.ffill(df, columns)
        return strategy_map[strategy](df, columns)


class DuplicateRemover:
    def remove_exact_duplicates(self, df: pl.DataFrame, subset: list = None) -> pl.DataFrame:
        if df.is_empty():
            return df
        if subset is not None:
            missing = [c for c in subset if c not in df.columns]
            if missing:
                warnings.warn(f"Subset columns not found: {missing}")
                subset = [c for c in subset if c in df.columns]
                if not subset:
                    return df
        return df.unique(subset=subset, keep='first', maintain_order=True)

    def remove_partial_duplicates(self, df: pl.DataFrame, key_columns: list, keep: str = 'last') -> pl.DataFrame:
        if df.is_empty():
            return df
        if not key_columns:
            warnings.warn("No key columns provided, returning original DataFrame")
            return df
        missing = [c for c in key_columns if c not in df.columns]
        if missing:
            warnings.warn(f"Key columns not found: {missing}")
            key_columns = [c for c in key_columns if c in df.columns]
            if not key_columns:
                return df
        return df.unique(subset=key_columns, keep=keep, maintain_order=True)


class DataQualityScorer:
    def score(self, df: pl.DataFrame) -> dict:
        if df.is_empty():
            return {'completeness': 0.0, 'consistency': 0.0, 'timeliness': 0.0, 'composite': 0.0}

        total_cells = df.shape[0] * df.shape[1]
        null_count = sum(df[c].null_count() for c in df.columns)
        completeness = 1.0 - (null_count / total_cells) if total_cells > 0 else 0.0

        numeric_cols = [c for c in df.columns if df[c].dtype.is_numeric()]
        if numeric_cols:
            outlier_count = 0
            total_values = 0
            for col_name in numeric_cols:
                col = df[col_name].drop_nulls()
                if col.len() < 4:
                    continue
                q1 = col.quantile(0.25)
                q3 = col.quantile(0.75)
                iqr_val = q3 - q1
                if iqr_val == 0:
                    continue
                lower = q1 - 1.5 * iqr_val
                upper = q3 + 1.5 * iqr_val
                outliers = ((col < lower) | (col > upper)).sum()
                outlier_count += outliers
                total_values += col.len()
            consistency = 1.0 - (outlier_count / total_values) if total_values > 0 else 1.0
        else:
            consistency = 1.0

        timeliness = self._compute_timeliness(df)

        composite = completeness + consistency + timeliness

        return {
            'completeness': round(completeness, 4),
            'consistency': round(consistency, 4),
            'timeliness': round(timeliness, 4),
            'composite': round(composite, 4),
        }

    def _compute_timeliness(self, df: pl.DataFrame) -> float:
        date_cols = [c for c in df.columns if df[c].dtype in (pl.Date, pl.Datetime)]
        if not date_cols:
            return 1.0

        col_name = date_cols[0]
        max_val = df[col_name].drop_nulls().max()
        if max_val is None:
            return 0.5

        now = datetime.datetime.now()
        if isinstance(max_val, datetime.datetime):
            delta = now - max_val
            days = max(delta.days, 0)
        elif isinstance(max_val, datetime.date):
            today = datetime.date.today()
            delta = today - max_val
            days = max(delta.days, 0)
        else:
            return 1.0

        if days <= 1:
            return 1.0
        elif days <= 7:
            return 0.9
        elif days <= 30:
            return 0.75
        elif days <= 90:
            return 0.5
        else:
            return round(max(0.1, 1.0 - days / 365), 4)


class DataCleaner:
    def __init__(self, outlier_method='iqr', fill_method='ffill', remove_duplicates=True):
        self.outlier_method = outlier_method
        self.fill_method = fill_method
        self.remove_duplicates = remove_duplicates
        self._outlier_detector = OutlierDetector()
        self._missing_handler = MissingValueHandler()
        self._dup_remover = DuplicateRemover()
        self._scorer = DataQualityScorer()

    def clean(self, df: pl.DataFrame) -> pl.DataFrame:
        result = df
        if self.remove_duplicates:
            result = self._dup_remover.remove_exact_duplicates(result)
        numeric_cols = [c for c in result.columns if result[c].dtype.is_numeric()]
        if numeric_cols:
            outlier_methods = {
                'iqr': self._outlier_detector.iqr,
                'zscore': self._outlier_detector.zscore,
                'mad': self._outlier_detector.mad,
            }
            method = outlier_methods.get(self.outlier_method, self._outlier_detector.iqr)
            result = method(result, numeric_cols)
        result = self._missing_handler.auto_fill(result, result.columns, strategy=self.fill_method)
        return result

    def clean_with_report(self, df: pl.DataFrame) -> tuple:
        cleaned = self.clean(df)
        report = self._scorer.score(cleaned)
        return cleaned, report


def _resolve_columns(df: pl.DataFrame, columns: list, context: str = "") -> list:
    existing = [c for c in columns if c in df.columns]
    missing = [c for c in columns if c not in df.columns]
    if missing:
        warnings.warn(f"[{context}] Columns not found in DataFrame: {missing}")
    return existing
