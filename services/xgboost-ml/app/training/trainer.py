"""
Trainer
Prepares training data and trains XGBoost models.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Tuple, Optional
from sklearn.model_selection import train_test_split

logger = logging.getLogger(__name__)


class Trainer:
    """Handles model training lifecycle."""

    def __init__(self, storage, feature_pipeline):
        self.storage = storage
        self.feature_pipeline = feature_pipeline

    def prepare_training_data(self) -> Tuple:
        """
        Prepare features and labels for training.
        
        Returns:
            (X_train, X_val, y_train, y_val) tuple or (None, None, None, None) if insufficient data
        """
        try:
            # Get training data from storage
            data = self.storage.get_training_data(days=365)

            if data is None or len(data) < 100:
                logger.warning(f"Insufficient training data: {len(data) if data is not None else 0}")
                return (None, None, None, None)

            # Features: use market data columns + return and volatility
            feature_cols = [
                "return_5d", "return_20d", "volatility_20d",
                "volume_ratio", "ma_cross",
            ]

            # Only use columns that exist
            feature_cols = [c for c in feature_cols if c in data.columns]

            if len(feature_cols) < 3:
                logger.warning(f"Too few features: {feature_cols}")
                return (None, None, None, None)

            X = data[feature_cols].values
            y = data["label"].values if "label" in data.columns else None

            if y is None:
                # Create labels: 1 if next day return > 0
                y = (data["future_return"].values > 0).astype(int)

            # Handle NaN
            mask = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
            X = X[mask]
            y = y[mask]

            if len(X) < 50:
                logger.warning(f"Too few valid samples: {len(X)}")
                return (None, None, None, None)

            # Split
            X_train, X_val, y_train, y_val = train_test_split(
                X, y, test_size=0.2, random_state=42, shuffle=False
            )

            logger.info(f"Training data: {len(X_train)} train, {len(X_val)} val, {len(feature_cols)} features")
            return (X_train, X_val, y_train, y_val)

        except Exception as e:
            logger.error(f"Failed to prepare training data: {e}")
            return (None, None, None, None)

    def train(self, model, X_train, y_train, X_val, y_val) -> Dict:
        """Train the model."""
        return model.train(X_train, y_train, X_val, y_val)
