"""
XGBoost ML Service
- Builds features from pgvector + Neo4j + yfinance + sentiment
- Trains XGBoost model for stock direction prediction
- Runs daily predictions and publishes to Redis
"""

import logging
import schedule
import time
import os
from datetime import datetime

from app.config import Config
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.xgboost_model import XGBoostModel
from app.models.model_manager import ModelManager
from app.training.trainer import Trainer
from app.inference.predictor import Predictor
from app.storage.postgres_storage import PostgresStorage
from app.metrics_integration import init_metrics, on_features_computed, on_prediction

logging.basicConfig(level=Config.LOG_LEVEL)
logger = logging.getLogger(__name__)


class XGBoostMLService:
    def __init__(self):
        logger.info("Initializing XGBoost ML Service...")
        self.config = Config()
        self.pg_storage = PostgresStorage()
        self.feature_pipeline = FeaturePipeline()
        self.model_manager = ModelManager(self.pg_storage)
        self.model = XGBoostModel()
        self.trainer = Trainer(self.pg_storage, self.feature_pipeline)
        self.predictor = Predictor(self.pg_storage, self.feature_pipeline, self.model)
        init_metrics(9102)
        self._running = False

    def initialize(self):
        """Initialize model - load existing or train new."""
        model_path = os.path.join(self.config.MODEL_PATH, f"xgboost_{self.config.MODEL_VERSION}.joblib")

        if os.path.exists(model_path):
            logger.info(f"Loading existing model: {model_path}")
            self.model.load(model_path)
        else:
            logger.info("No existing model found. Training new model...")
            self.train_model()

    def train_model(self):
        """Train or retrain the XGBoost model."""
        logger.info("Starting model training...")

        try:
            # Prepare training data
            X_train, X_val, y_train, y_val = self.trainer.prepare_training_data()

            if X_train is None:
                logger.warning("Insufficient training data")
                return

            # Train model
            metrics = self.trainer.train(self.model, X_train, y_train, X_val, y_val)

            # Save model
            model_path = os.path.join(
                self.config.MODEL_PATH,
                f"xgboost_{self.config.MODEL_VERSION}.joblib"
            )
            os.makedirs(self.config.MODEL_PATH, exist_ok=True)
            self.model.save(model_path)

            # Track model version
            self.model_manager.save_model_version(
                version=self.config.MODEL_VERSION,
                metrics=metrics,
            )

            logger.info(f"Training complete. Metrics: {metrics}")

        except Exception as e:
            logger.error(f"Training failed: {e}")

    def run_predictions(self):
        """Run daily predictions for all stocks."""
        logger.info("Running daily predictions...")

        stocks = self.pg_storage.get_all_stocks()
        predictions = []

        import time as _time
        _t0_f = _time.time()
        for stock in stocks:
            try:
                _t0 = _time.time()
                prediction = self.predictor.predict(stock["stock_code"])
                on_prediction(_time.time() - _t0)
                if prediction and prediction["confidence"] >= self.config.PREDICTION_CONFIDENCE_THRESHOLD:
                    predictions.append(prediction)
            except Exception as e:
                logger.debug(f"Prediction failed for {stock['stock_code']}: {e}")
                continue

        # Store predictions
        for pred in predictions:
            self.pg_storage.save_prediction(pred)

        # Publish high-confidence predictions
        top_predictions = sorted(
            predictions, key=lambda x: x["confidence"], reverse=True
        )[:10]
        self.predictor.publish_signals_to_redis(top_predictions)

        logger.info(f"Predictions complete. Generated {len(predictions)} predictions.")
        on_features_computed(len(predictions))

    def run_scheduled(self):
        """Run on schedule."""
        schedule.every().day.at("19:00").do(self.run_predictions)

        # Retrain weekly
        schedule.every(self.config.RETRAIN_INTERVAL_DAYS).days.do(self.train_model)

        logger.info("ML Service started. Predictions daily at 19:00.")
        self._running = True

        # Initialize on startup
        self.initialize()

        # Run prediction once
        self.run_predictions()

        while self._running:
            schedule.run_pending()
            time.sleep(60)

    def stop(self):
        self._running = False


def main():
    service = XGBoostMLService()
    try:
        service.run_scheduled()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        service.stop()


if __name__ == "__main__":
    main()
