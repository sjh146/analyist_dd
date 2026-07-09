"""
Trainer
Prepares training data with time-series split and trains XGBoost models.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Tuple, Optional
from datetime import datetime, timedelta

from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

logger = logging.getLogger(__name__)


class Trainer:
    """Handles model training lifecycle with time-series split and binary labeling."""

    def __init__(self, storage, feature_pipeline):
        self.storage = storage
        self.feature_pipeline = feature_pipeline

    def prepare_training_data(
        self, stock_codes: list = None, days: int = 365
    ) -> Tuple:
        """
        Prepare features and labels for training using time-series split.

        Returns:
            (X_train, X_val, X_test, y_train, y_val, y_test) or None tuple on failure
        """
        if stock_codes is None:
            stock_codes = self._get_stock_list()

        if not stock_codes:
            logger.warning("No stocks available for training")
            return (None, None, None, None, None, None)

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        try:
            df = self.feature_pipeline.build_training_features(
                stock_codes,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
            )

            if df is None or len(df) < 100:
                logger.warning(f"Insufficient training data: {len(df) if df is not None else 0} rows")
                return (None, None, None, None, None, None)

            feature_names = self.feature_pipeline.get_feature_names()
            available_features = [c for c in feature_names if c in df.columns]

            if len(available_features) < 10:
                logger.warning(f"Too few features available: {available_features}")
                return (None, None, None, None, None, None)

            X = df[available_features].values.astype(np.float32)
            y = self._create_labels(df)

            mask = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
            X = X[mask]
            y = y[mask]

            if len(X) < 50:
                logger.warning(f"Too few valid samples after NaN removal: {len(X)}")
                return (None, None, None, None, None, None)

            n = len(X)
            train_end = int(n * 0.60)
            val_end = int(n * 0.80)

            X_train, y_train = X[:train_end], y[:train_end]
            X_val, y_val = X[train_end:val_end], y[train_end:val_end]
            X_test, y_test = X[val_end:], y[val_end:]

            logger.info(
                f"Training data prepared: {len(X_train)} train, "
                f"{len(X_val)} val, {len(X_test)} test, "
                f"{len(available_features)} features"
            )
            return (X_train, X_val, X_test, y_train, y_val, y_test)

        except Exception as e:
            logger.error(f"Failed to prepare training data: {e}")
            return (None, None, None, None, None, None)

    def train(self, model, X_train, y_train, X_val, y_val) -> Dict:
        """Train the model and return metrics."""
        metrics = model.train(X_train, y_train, X_val, y_val)

        if X_val is not None and y_val is not None:
            val_preds = (model.predict(X_val) > 0.5).astype(int)
            metrics["precision"] = float(precision_score(y_val, val_preds, zero_division=0))
            metrics["recall"] = float(recall_score(y_val, val_preds, zero_division=0))
            metrics["f1"] = float(f1_score(y_val, val_preds, zero_division=0))
            try:
                metrics["roc_auc"] = float(roc_auc_score(y_val, model.predict(X_val)))
            except ValueError:
                metrics["roc_auc"] = 0.5

        if X_train is not None and y_train is not None:
            train_preds = (model.predict(X_train) > 0.5).astype(int)
            metrics["train_accuracy"] = float(accuracy_score(y_train, train_preds))

        logger.info(f"Training metrics: {metrics}")
        return metrics

    def _create_labels(self, df: pd.DataFrame) -> np.ndarray:
        """
        Create binary labels: 1 if next-day close > current close, else 0.
        Labels must NOT use future data — uses shift(-1) within each stock group.
        """
        if "label" in df.columns and df["label"].notna().any():
            return df["label"].values.astype(int)

        labels = np.zeros(len(df), dtype=int)

        if "stock_code" in df.columns and "price" in df.columns:
            for code in df["stock_code"].unique():
                mask = df["stock_code"] == code
                idx = df[mask].index
                prices = df.loc[idx, "price"].values
                if len(prices) >= 2:
                    next_up = prices[1:] > prices[:-1]
                    label_vals = np.zeros(len(prices), dtype=int)
                    label_vals[:-1] = next_up.astype(int)
                    labels[idx] = label_vals

        return labels

    def _get_stock_list(self) -> list:
        """Get list of tracked stock codes from storage."""
        try:
            stocks = self.storage.get_all_stocks()
            return [s["stock_code"] for s in stocks] if stocks else ["005930"]
        except Exception:
            return ["005930"]
