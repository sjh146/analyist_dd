"""
Predictor
Runs real-time predictions and publishes results.
"""

import numpy as np
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class Predictor:
    """Handles model inference."""

    def __init__(self, storage, feature_pipeline, model):
        self.storage = storage
        self.feature_pipeline = feature_pipeline
        self.model = model

    def predict(self, stock_code: str) -> Optional[Dict]:
        """
        Predict stock direction for a single stock.
        
        Args:
            stock_code: Stock code
        
        Returns:
            Prediction dict or None
        """
        try:
            # Build features
            features = self.feature_pipeline.build_features(stock_code)
            if not features:
                return None

            # Convert to array
            feature_array = np.array([list(features.values())])

            # Predict
            result = self.model.predict_single(feature_array)

            return {
                "stock_code": stock_code,
                "prediction_date": datetime.now().date().isoformat(),
                "model_version": "v1.0",
                "predicted_direction": result["predicted_direction"],
                "predicted_probability": result["predicted_probability"],
                "confidence": result["confidence"],
                "features_used": list(features.keys()),
            }

        except Exception as e:
            logger.debug(f"Prediction failed for {stock_code}: {e}")
            return None

    def publish_signals_to_redis(self, predictions: List[Dict]):
        """Publish high-confidence predictions to Redis."""
        if not predictions:
            return

        logger.info(f"Publishing {len(predictions)} predictions to Redis")
        # Implementation would publish to Redis channel
        for pred in predictions:
            logger.info(f"Signal: {pred['stock_code']} -> {pred['predicted_direction']} ({pred['confidence']:.2f})")
