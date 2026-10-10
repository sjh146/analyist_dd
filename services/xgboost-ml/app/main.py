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
from app.inference.predictor import Predictor, filter_prediction_universe
from app.inference.day_guard import (
    check_and_report_degenerate_day,
    check_and_report_prior_run,
    check_and_report_stale_model,
    champion_newest_mtime,
)
from app.storage.postgres_storage import PostgresStorage
from app.metrics_integration import init_metrics, on_features_computed, on_prediction, on_feature_count

logging.basicConfig(level=Config.LOG_LEVEL)
logger = logging.getLogger(__name__)


class XGBoostMLService:
    def __init__(self):
        logger.info("Initializing XGBoost ML Service...")
        self.config = Config()
        self.pg_storage = PostgresStorage()
        self.feature_pipeline = FeaturePipeline(pg_conn=self.pg_storage._get_conn())
        self.model_manager = ModelManager(self.pg_storage)
        self.model = XGBoostModel()
        self.trainer = Trainer(self.pg_storage, self.feature_pipeline)
        self.predictor = Predictor(self.pg_storage, self.feature_pipeline, self.model)
        init_metrics(9102)
        self._running = False

    def initialize(self):
        """Initialize model - load existing or train new."""
        model_path = os.path.join(self.config.MODEL_PATH, "xgboost_model.pkl")

        if os.path.exists(model_path):
            logger.info(f"Loading existing model: {model_path}")
            self.model.load(model_path)
        else:
            logger.info("No existing model found. Training new model...")
            self.train_model()

        # 승격≠재기동 가드(CG163, 2026-10-11): 프로세스가 **로드한** champion 산출물의 mtime 을
        # 기록해 둔다. 이후 champion/ 이 이 시각 뒤에 바뀌면 살아 있는 프로세스는 옛 모델로 채점
        # 중이다(모델은 기동 시 1회만 로드되고 파일 감시가 없다). 감지·표시 전용.
        try:
            self._champion_loaded_mtime = champion_newest_mtime(self.config.MODEL_PATH)
        except Exception as _e:  # 가드가 기동을 막지 않는다
            logger.debug(f"champion mtime 기록 실패(CG163 가드 생략됨): {_e}")
            self._champion_loaded_mtime = None

        # feature_count_gauge 백필 — 챔피언 피처 수 노출 (Grafana Feature Count 패널,
        # 2026-08: 게이지 시리즈가 없어 No data 표시되던 문제 수정)
        try:
            fn_path = os.path.join(self.config.MODEL_PATH, "feature_names.json")
            if os.path.exists(fn_path):
                import json as _json

                with open(fn_path) as _f:
                    on_feature_count("champion", len(_json.load(_f)))
        except Exception as e:
            logger.debug(f"feature count gauge failed: {e}")

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
                "xgboost_model.pkl"
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

        # 배포 추론 유니버스(CG159): 기본 OFF = 종전과 비트 동일. 켜면 ETF/ETN 제외.
        stocks = filter_prediction_universe(self.pg_storage.get_all_stocks())
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

        # 퇴화일 가드(CG160, 2026-10-10): 하루 전체 예측이 단일 상수면(실측 2026-09-22: 2,678행 전부
        # 0.1429 = 그날 AUC 정의상 0.5) 로그 CRITICAL + 증거 파일 `degenerate_day_<날짜>.json` 을 남긴다.
        # **발행 목록은 변경하지 않는다** — 감지·표시 전용이라 발행 계약 무변경(승인 불필요 범위).
        check_and_report_degenerate_day(predictions)

        # 재실행 혼합 가드(CG161, 2026-10-11): 같은 prediction_date 에 이미 행이 있으면 경보.
        # `save_prediction` 이 ON CONFLICT DO NOTHING 이라 **먼저 들어간 행이 고정**되고, 하루에 두 번
        # 실행되면 그 날짜의 행 집합이 두 실행 시점의 혼합 스냅샷이 된다(실측 2026-09-23: 02:15
        # 2,770행 μ0.2843 + 19:43 1,544행 μ0.1459). 감지·표시 전용 — DB·발행 목록 불변.
        if predictions:
            _existing = None
            try:
                _existing = self.pg_storage.count_predictions_for_date(
                    predictions[0]["prediction_date"]
                )
            except Exception as _e:  # 가드가 발행을 막지 않는다
                logger.debug("기존 예측 행수 조회 실패(CG161 가드 생략): %s", _e)
            check_and_report_prior_run(_existing, len(predictions))

        # 승격≠재기동 가드(CG163, 2026-10-11): champion/ 이 프로세스 로드 이후에 바뀌었으면
        # (실측 2026-10-02~10-08: 승격 후 재기동 없이 7일간 옛 모델로 채점 → 전 종목 confidence
        # 최대 < 0.30, 소비 문턱 0.55 도달 0행) 로그 CRITICAL + `stale_model_<날짜>.json` 을 남긴다.
        # **모델·발행 목록은 변경하지 않는다** — 감지·표시 전용이라 발행 계약 무변경(승인 불필요 범위).
        try:
            check_and_report_stale_model(
                self.config.MODEL_PATH,
                loaded_mtime=getattr(self, "_champion_loaded_mtime", None),
            )
        except Exception as _e:  # 가드가 발행을 막지 않는다
            logger.debug("승격≠재기동 가드 생략(CG163): %s", _e)

        # Store predictions.  한 건의 저장 실패가 루프 전체를 중단시키면 안 된다
        # (2026-09-28: Postgres 가 연결을 끊자 예외가 새어나가 그날 예측이 0행이 됐다).
        saved = 0
        failed = 0
        for pred in predictions:
            if self.pg_storage.save_prediction(pred):
                saved += 1
            else:
                failed += 1
        if failed:
            logger.warning(
                "ml_predictions 저장: %s/%s 성공 (%s건 실패 — 재시도 후에도 실패한 건은 로그 참조)",
                saved, len(predictions), failed,
            )
        else:
            logger.info("ml_predictions 저장: %s/%s 성공", saved, len(predictions))

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

        # Initialize on startup (모델 로드 먼저 — 예측 전 필수)
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
