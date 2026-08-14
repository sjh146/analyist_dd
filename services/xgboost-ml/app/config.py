import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB = os.getenv("POSTGRES_DB", "stock_trading")
    POSTGRES_USER = os.getenv("POSTGRES_USER", "stock_user")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    MODEL_VERSION = os.getenv("ML_MODEL_VERSION", "v1.0")
    # 단일 챔피언 계약: 모든 소비자(스크리너/백테스터/예측기)가 champion/ 사용.
    # saved_models/ 는 2026-08 제거됨 (버전별 실험 산출물 보존 정책 폐기).
    MODEL_PATH = "/app/app/models/champion"
    RETRAIN_INTERVAL_DAYS = int(os.getenv("ML_RETRAIN_INTERVAL_DAYS", "7"))
    PREDICTION_CONFIDENCE_THRESHOLD = 0.0
