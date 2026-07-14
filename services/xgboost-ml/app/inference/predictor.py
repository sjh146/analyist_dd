"""
Predictor
Runs daily inference and publishes high-confidence signals to Redis.
"""

import json
import logging
import os
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import redis
except ImportError:
    redis = None

try:
    from services.shared.redis_streams import RedisStreams
except ImportError:
    RedisStreams = None


class Predictor:
    """Runs predictions for all tracked stocks and publishes signals."""

    def __init__(self, storage, feature_pipeline, model, redis_client=None):
        self.storage = storage
        self.feature_pipeline = feature_pipeline
        self.model = model
        self.redis_client = redis_client or self._create_redis_client()
        self._streams = None
        if RedisStreams:
            try:
                host = os.environ.get("REDIS_HOST", "redis")
                port = int(os.environ.get("REDIS_PORT", 6379))
                self._streams = RedisStreams(f"redis://{host}:{port}")
            except Exception:
                pass

    def predict(self, stock_code: str, date: str = None) -> Optional[Dict]:
        """Predict direction for a single stock."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        try:
            features = self.feature_pipeline.build_features(stock_code, date)
            feature_names = self.feature_pipeline.get_feature_names()
            feature_vector = np.array([
                features.get(f, 0.0) for f in feature_names
            ], dtype=np.float32)

            if np.isnan(feature_vector).any():
                feature_vector = np.nan_to_num(feature_vector, nan=0.0)

            result = self.model.predict_single(feature_vector)

            return {
                "stock_code": stock_code,
                "prediction_date": date,
                "model_version": os.environ.get("ML_MODEL_VERSION", "v1.0"),
                "direction": result["predicted_direction"],
                "confidence": float(result["confidence"]),
                "probability": float(result["predicted_probability"]),
            }

        except Exception as e:
            logger.debug(f"Prediction failed for {stock_code}: {e}")
            return None

    def predict_all(self) -> List[Dict]:
        """Run predictions for all tracked stocks."""
        stocks = self.storage.get_all_stocks()
        predictions = []

        for stock in stocks:
            pred = self.predict(stock["stock_code"])
            if pred:
                predictions.append(pred)

        logger.info(f"Generated {len(predictions)} predictions")
        return predictions

    def _get_signal_stream_name(self) -> str:
        return os.environ.get("REDIS_SIGNAL_STREAM", "trading:signals")

    def publish_signals_to_redis(self, predictions: List[Dict]):
        """Publish top predictions to Redis Streams."""
        if not self._streams:
            logger.warning("Redis Streams not available; skipping signal publish")
            return

        filtered = [
            p for p in predictions
            if p["confidence"] >= 0.6 and p["direction"] in ("up", "down")
        ]
        top = sorted(filtered, key=lambda x: x["confidence"], reverse=True)[:10]

        stream_name = self._get_signal_stream_name()

        for pred in top:
            try:
                direction = pred["direction"]
                timestamp = datetime.now().isoformat()

                signal_data = {
                    "stock_code": pred["stock_code"],
                    "signal": "buy" if direction == "up" else "sell",
                    "confidence": pred["confidence"],
                    "timestamp": timestamp,
                    "model_version": os.environ.get("ML_MODEL_VERSION", "v1.0"),
                }

                self._streams.xadd(stream_name, signal_data, maxlen=10000)
                logger.info(
                    f"Signal streamed: {pred['stock_code']} "
                    f"{direction} ({pred['confidence']:.2f})"
                )
            except Exception as e:
                logger.error(f"Redis Streams xadd failed for {pred['stock_code']}: {e}")

        logger.info(f"Published {len(top)} signals to Redis Streams")

    def _create_redis_client(self):
        """Create Redis client from environment config."""
        if not redis:
            return None
        try:
            host = os.environ.get("REDIS_HOST", "redis")
            port = int(os.environ.get("REDIS_PORT", 6379))
            password = os.environ.get("REDIS_PASSWORD", "")
            return redis.Redis(
                host=host, port=port, password=password,
                decode_responses=True, socket_connect_timeout=5,
            )
        except Exception:
            return None
