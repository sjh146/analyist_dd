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
            (X_train, X_val, X_test, y_train, y_val, y_test, feature_names) or None tuple on failure
        """
        if stock_codes is None:
            stock_codes = self._get_stock_list()

        if not stock_codes:
            logger.warning("No stocks available for training")
            return (None, None, None, None, None, None, None)

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
                return (None, None, None, None, None, None, None)

            feature_names = self.feature_pipeline.get_feature_names()
            available_features = [c for c in feature_names if c in df.columns]

            if len(available_features) < 10:
                logger.warning(f"Too few features available: {available_features}")
                return (None, None, None, None, None, None, None)

            # Sort chronologically for time-series integrity
            if 'date' in df.columns:
                df = df.sort_values('date').reset_index(drop=True)
            elif 'trade_date' in df.columns:
                df = df.sort_values('trade_date').reset_index(drop=True)

            # --- Feature engineering: interaction features and rolling stats ---
            interaction_features = []

            # Interaction pairs: (feat_a, feat_b, new_name)
            interaction_pairs = [
                ("return_1d", "volatility_20d", "momentum_vs_volatility"),
                ("return_5d", "return_20d", "trend_interaction"),
                ("volume_ratio_5", "return_5d", "volume_price_trend"),
                ("ma_position_5", "ma_position_20", "cross_trend"),
                ("volatility_20d", "volume_ratio_5", "volatility_volume"),
                ("return_1d", "return_5d", "short_medium_term_momentum"),
                ("return_5d", "ma_position_20", "trend_confirmation"),
                ("price", "volume_ratio_5", "price_volume"),
            ]

            for a, b, name in interaction_pairs:
                if a in df.columns and b in df.columns:
                    df[name] = df[a] * df[b]
                    interaction_features.append(name)

            # Rolling statistics (per stock_code group)
            rolling_stats = [
                ("return_5d", "return_5d_mean_10d", 10),
                ("volatility_20d", "volatility_20d_mean_10d", 10),
                ("volume_ratio_5", "volume_ratio_5_mean_10d", 10),
            ]

            if "stock_code" in df.columns:
                for col, new_name, window in rolling_stats:
                    if col in df.columns:
                        df[new_name] = df.groupby("stock_code")[col].transform(
                            lambda x: x.rolling(window=window, min_periods=1).mean()
                        )
                        interaction_features.append(new_name)
            else:
                for col, new_name, window in rolling_stats:
                    if col in df.columns:
                        df[new_name] = df[col].rolling(window=window, min_periods=1).mean()
                        interaction_features.append(new_name)

            # --- Cross-sectional percentile rank features (rank each stock against all others on same date) ---
            cross_sectional_rank_cols = [
                "return_5d", "return_20d", "volatility_20d",
                "volume_ratio_5", "ma_position_5", "volume_ratio_20",
            ]
            date_col = "date" if "date" in df.columns else ("trade_date" if "trade_date" in df.columns else None)
            if date_col is not None:
                for col in cross_sectional_rank_cols:
                    if col in df.columns:
                        rank_name = f"rank_{col}"
                        df[rank_name] = df.groupby(date_col)[col].rank(pct=True)
                        interaction_features.append(rank_name)

            # --- Rolling target encoding (rolling mean of return_1d as proxy for future return) ---
            target_ma_windows = [
                ("return_1d", "target_ma_5", 5),
                ("return_1d", "target_ma_10", 10),
                ("return_1d", "target_ma_20", 20),
            ]
            if "stock_code" in df.columns and "return_1d" in df.columns:
                for col, new_name, window in target_ma_windows:
                    df[new_name] = df.groupby("stock_code")["return_1d"].transform(
                        lambda x: x.rolling(window=window, min_periods=1).mean()
                    )
                    interaction_features.append(new_name)

            # Extend available_features with new interaction features
            available_features.extend(interaction_features)

            X = df[available_features].values.astype(np.float32)
            y = self._create_labels(df)

            X = np.nan_to_num(X, nan=0.0)
            valid = ~np.isnan(y)
            X = X[valid]
            y = y[valid]

            if len(X) < 50:
                logger.warning(f"Too few valid samples after NaN removal: {len(X)}")
                return (None, None, None, None, None, None, None)

            # Remove constant features (zero variance)
            col_stds = np.std(X, axis=0)
            varying_mask = col_stds > 0
            if varying_mask.sum() < 5:
                logger.warning(f"Too few varying features: {varying_mask.sum()}")
                return (None, None, None, None, None, None, None)
            X = X[:, varying_mask]
            available_features = [f for f, m in zip(available_features, varying_mask) if m]
            logger.info(f"Features after variance filter: {len(available_features)}")

            # Check label balance
            n_pos = int(y.sum())
            n_neg = len(y) - n_pos
            logger.info(f"Label balance: {n_pos} up ({100*n_pos/len(y):.1f}%), {n_neg} down ({100*n_neg/len(y):.1f}%)")

            n = len(X)
            train_end = int(n * 0.60)
            val_end = int(n * 0.80)

            X_train, y_train = X[:train_end], y[:train_end]
            X_val, y_val = X[train_end:val_end], y[train_end:val_end]
            X_test, y_test = X[val_end:], y[val_end:]

            # Log per-split balance
            for name, ys in [("train", y_train), ("val", y_val), ("test", y_test)]:
                p = int(ys.sum())
                logger.info(f"  {name}: {len(ys)} samples, {p} up ({100*p/max(len(ys),1):.1f}%)")

            logger.info(
                f"Training data prepared: {len(X_train)} train, "
                f"{len(X_val)} val, {len(X_test)} test, "
                f"{len(available_features)} features"
            )
            return (X_train, X_val, X_test, y_train, y_val, y_test, available_features)

        except Exception as e:
            logger.error(f"Failed to prepare training data: {e}")
            return (None, None, None, None, None, None, None)

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

    def walk_forward_validate(
        self, stock_codes=None, days=365, window_days=60,
        step_days=20, purge_days=5, embargo_days=2,
        model_cls=None,
    ) -> Dict:
        """
        Walk-forward cross-validation to eliminate look-ahead bias.

        For each validation window:
          Train period: [start, start + window_days)
          Test period:  [start + window_days + purge_days,
                         start + window_days + purge_days + step_days)
          Window slides forward by step_days.

        Args:
            stock_codes: List of stock codes to validate on.
            days: Lookback calendar days for the full dataset.
            window_days: Trading days per training window.
            step_days: Trading days to slide forward per window.
            purge_days: Gap trading days between train/test to prevent leakage.
            embargo_days: Additional embargo (reserved; purge provides gap).
            model_cls: Model class to instantiate per window (default: XGBoostModel).

        Returns:
            Dict with:
              'windows': list of per-window metric dicts
              'avg_metrics': averaged metrics across windows
              'n_windows': number of windows completed
              'error': error message on failure (absent on success)
        """
        if stock_codes is None:
            stock_codes = self._get_stock_list()

        if not stock_codes:
            logger.warning("No stocks available for walk-forward validation")
            return {"error": "No stock codes provided", "windows": [], "avg_metrics": {}, "n_windows": 0}

        from app.models.xgboost_model import XGBoostModel
        if model_cls is None:
            model_cls = XGBoostModel

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        try:
            df = self.feature_pipeline.build_training_features(
                stock_codes,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
            )

            if df is None or len(df) < 100:
                msg = f"Insufficient training data: {len(df) if df is not None else 0} rows"
                logger.warning(msg)
                return {"error": msg, "windows": [], "avg_metrics": {}, "n_windows": 0}

            feature_names = self.feature_pipeline.get_feature_names()
            available_features = [c for c in feature_names if c in df.columns]

            if len(available_features) < 10:
                msg = f"Too few features available: {len(available_features)}"
                logger.warning(msg)
                return {"error": msg, "windows": [], "avg_metrics": {}, "n_windows": 0}

            date_col = "date" if "date" in df.columns else ("trade_date" if "trade_date" in df.columns else None)
            if date_col is None:
                return {"error": "No date column in training data", "windows": [], "avg_metrics": {}, "n_windows": 0}

            df = df.sort_values(date_col).reset_index(drop=True)

            interaction_features = []

            interaction_pairs = [
                ("return_1d", "volatility_20d", "momentum_vs_volatility"),
                ("return_5d", "return_20d", "trend_interaction"),
                ("volume_ratio_5", "return_5d", "volume_price_trend"),
                ("ma_position_5", "ma_position_20", "cross_trend"),
                ("volatility_20d", "volume_ratio_5", "volatility_volume"),
                ("return_1d", "return_5d", "short_medium_term_momentum"),
                ("return_5d", "ma_position_20", "trend_confirmation"),
                ("price", "volume_ratio_5", "price_volume"),
            ]

            for a, b, name in interaction_pairs:
                if a in df.columns and b in df.columns:
                    df[name] = df[a] * df[b]
                    interaction_features.append(name)

            rolling_stats = [
                ("return_5d", "return_5d_mean_10d", 10),
                ("volatility_20d", "volatility_20d_mean_10d", 10),
                ("volume_ratio_5", "volume_ratio_5_mean_10d", 10),
            ]

            if "stock_code" in df.columns:
                for col, new_name, window in rolling_stats:
                    if col in df.columns:
                        df[new_name] = df.groupby("stock_code")[col].transform(
                            lambda x: x.rolling(window=window, min_periods=1).mean()
                        )
                        interaction_features.append(new_name)
            else:
                for col, new_name, window in rolling_stats:
                    if col in df.columns:
                        df[new_name] = df[col].rolling(window=window, min_periods=1).mean()
                        interaction_features.append(new_name)

            cross_sectional_rank_cols = [
                "return_5d", "return_20d", "volatility_20d",
                "volume_ratio_5", "ma_position_5", "volume_ratio_20",
            ]
            if date_col is not None:
                for col in cross_sectional_rank_cols:
                    if col in df.columns:
                        rank_name = f"rank_{col}"
                        df[rank_name] = df.groupby(date_col)[col].rank(pct=True)
                        interaction_features.append(rank_name)

            target_ma_windows = [
                ("return_1d", "target_ma_5", 5),
                ("return_1d", "target_ma_10", 10),
                ("return_1d", "target_ma_20", 20),
            ]
            if "stock_code" in df.columns and "return_1d" in df.columns:
                for _col, new_name, window in target_ma_windows:
                    df[new_name] = df.groupby("stock_code")["return_1d"].transform(
                        lambda x: x.rolling(window=window, min_periods=1).mean()
                    )
                    interaction_features.append(new_name)

            available_features.extend(interaction_features)

            unique_dates = sorted(df[date_col].unique())
            min_required = window_days + purge_days + step_days

            if len(unique_dates) < min_required:
                msg = (
                    f"Not enough trading days ({len(unique_dates)}) "
                    f"for even 1 walk-forward window (need {min_required})"
                )
                logger.warning(msg)
                return {"error": msg, "windows": [], "avg_metrics": {}, "n_windows": 0}

            max_start = len(unique_dates) - window_days - purge_days - step_days
            max_windows = max_start // step_days + 1

            all_window_metrics = []
            n_windows = 0

            for w in range(max_windows):
                start_idx = w * step_days

                train_end_idx = start_idx + window_days
                test_start_idx = start_idx + window_days + purge_days
                test_end_idx = test_start_idx + step_days

                if test_end_idx > len(unique_dates):
                    break

                train_dates_set = set(unique_dates[start_idx:train_end_idx])
                test_dates_set = set(unique_dates[test_start_idx:test_end_idx])

                train_mask = df[date_col].isin(train_dates_set)
                test_mask = df[date_col].isin(test_dates_set)

                train_df = df[train_mask].copy()
                test_df = df[test_mask].copy()

                if len(train_df) < 50 or len(test_df) < 10:
                    logger.warning(
                        f"Window {w}: insufficient train ({len(train_df)}) "
                        f"or test ({len(test_df)}) samples, skipping"
                    )
                    continue

                try:
                    X_train = train_df[available_features].values.astype(np.float32)
                    y_train = self._create_labels(train_df)

                    X_train = np.nan_to_num(X_train, nan=0.0)
                    y_train_float = y_train.astype(np.float64)
                    valid_train = ~np.isnan(y_train_float)
                    X_train = X_train[valid_train]
                    y_train = y_train[valid_train]

                    if len(X_train) < 50:
                        logger.warning(f"Window {w}: too few train samples after NaN removal ({len(X_train)}), skipping")
                        continue

                    col_stds = np.std(X_train, axis=0)
                    varying_mask = col_stds > 0
                    if varying_mask.sum() < 5:
                        logger.warning(f"Window {w}: too few varying features ({varying_mask.sum()}), skipping")
                        continue

                    X_train = X_train[:, varying_mask]
                    window_feature_names = [f for f, m in zip(available_features, varying_mask) if m]

                    X_test = test_df[available_features].values.astype(np.float32)
                    y_test = self._create_labels(test_df)

                    X_test = np.nan_to_num(X_test, nan=0.0)
                    y_test_float = y_test.astype(np.float64)
                    valid_test = ~np.isnan(y_test_float)
                    X_test = X_test[valid_test]
                    y_test = y_test[valid_test]

                    if len(X_test) < 10:
                        logger.warning(f"Window {w}: too few test samples after NaN removal ({len(X_test)}), skipping")
                        continue

                    X_test = X_test[:, varying_mask]

                    n_pos = int(y_train.sum())
                    n_neg = len(y_train) - n_pos
                    if n_pos == 0 or n_neg == 0:
                        logger.warning(f"Window {w}: only one class in training data ({n_pos} pos, {n_neg} neg), skipping")
                        continue

                    model = model_cls()
                    model.train(X_train, y_train)

                    probs = model.predict(X_test)
                    preds = (probs > 0.5).astype(int)

                    window_result = {
                        "window": w,
                        "train_start": str(unique_dates[start_idx]),
                        "train_end": str(unique_dates[train_end_idx - 1]),
                        "test_start": str(unique_dates[test_start_idx]),
                        "test_end": str(unique_dates[test_end_idx - 1]),
                        "train_samples": int(len(X_train)),
                        "test_samples": int(len(X_test)),
                        "accuracy": float(accuracy_score(y_test, preds)),
                        "precision": float(precision_score(y_test, preds, zero_division=0)),
                        "recall": float(recall_score(y_test, preds, zero_division=0)),
                        "f1": float(f1_score(y_test, preds, zero_division=0)),
                        "n_features": int(len(window_feature_names)),
                    }
                    try:
                        window_result["roc_auc"] = float(roc_auc_score(y_test, probs))
                    except ValueError:
                        window_result["roc_auc"] = 0.5

                    all_window_metrics.append(window_result)
                    n_windows += 1

                    logger.info(
                        f"Walk-forward window {w}: "
                        f"train={len(X_train)} test={len(X_test)} "
                        f"acc={window_result['accuracy']:.3f} "
                        f"auc={window_result['roc_auc']:.3f}"
                    )

                except Exception as e:
                    logger.warning(f"Walk-forward window {w} failed: {e}")
                    continue

            if n_windows == 0:
                return {
                    "error": "No windows completed successfully",
                    "windows": [], "avg_metrics": {}, "n_windows": 0,
                }

            avg_metrics = {
                "accuracy": float(np.mean([w["accuracy"] for w in all_window_metrics])),
                "precision": float(np.mean([w["precision"] for w in all_window_metrics])),
                "recall": float(np.mean([w["recall"] for w in all_window_metrics])),
                "f1": float(np.mean([w["f1"] for w in all_window_metrics])),
                "roc_auc": float(np.mean([w["roc_auc"] for w in all_window_metrics])),
            }

            logger.info(
                f"Walk-forward validation complete: {n_windows} windows, "
                f"avg acc={avg_metrics['accuracy']:.3f}, "
                f"avg auc={avg_metrics['roc_auc']:.3f}"
            )

            return {
                "windows": all_window_metrics,
                "avg_metrics": avg_metrics,
                "n_windows": n_windows,
            }

        except Exception as e:
            logger.error(f"Walk-forward validation failed: {e}")
            return {
                "error": str(e),
                "windows": [], "avg_metrics": {}, "n_windows": 0,
            }

    def _create_labels(self, df: pd.DataFrame) -> np.ndarray:
        """
        Create binary labels: 1 if next trading day's close > current close, else 0.
        Uses shift(-1) within each stock group. Last row of each group gets label 0
        (no 1-day future data available).
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
                    # Compare price[i] with price[i+1] — 1-day forward return
                    next_up = prices[1:] > prices[:-1]
                    label_vals = np.zeros(len(prices), dtype=int)
                    label_vals[:-1] = next_up.astype(int)
                    labels[idx] = label_vals
                # len(prices) < 2: all zeros (no 1-day future data)

        return labels

    def _get_stock_list(self) -> list:
        """Get list of tracked stock codes from storage."""
        try:
            stocks = self.storage.get_all_stocks()
            return [s["stock_code"] for s in stocks] if stocks else ["005930"]
        except Exception:
            return ["005930"]
