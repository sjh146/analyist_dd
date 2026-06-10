"""
Data Cleaner
Cleans and validates market data before storage.
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class DataCleaner:
    """Cleans and validates stock market data."""

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run full cleaning pipeline."""
        df = self.remove_outliers(df)
        df = self.fill_missing_values(df)
        df = self.validate_data(df)
        return df

    def remove_outliers(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove extreme outliers using IQR method."""
        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            if col in df.columns:
                Q1 = df[col].quantile(0.01)
                Q3 = df[col].quantile(0.99)
                IQR = Q3 - Q1
                lower = Q1 - 3 * IQR
                upper = Q3 + 3 * IQR
                df[col] = df[col].clip(lower, upper)
        return df

    def fill_missing_values(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fill missing values using forward fill and linear interpolation."""
        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = df.groupby("stock_code")[col].transform(
                    lambda x: x.ffill().bfill()
                )
        return df

    def validate_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Validate data quality and remove invalid rows."""
        # Remove rows with missing critical values
        before = len(df)
        df = df.dropna(subset=["open", "high", "low", "close"])
        after = len(df)

        if before > after:
            logger.warning(f"Removed {before - after} rows with missing values")

        # Remove rows with negative prices
        for col in ["open", "high", "low", "close"]:
            df = df[df[col] >= 0]

        # Remove rows with zero volume (trading halt)
        df = df[(df["volume"] > 0) | (df["volume"].isna())]

        return df
