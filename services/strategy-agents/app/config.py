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

    BRIDGE_VM_IP = os.getenv("BRIDGE_VM_IP", "192.168.1.101")
    BRIDGE_VM_PORT = int(os.getenv("BRIDGE_VM_PORT", "6379"))

    TRADING_START_HOUR = int(os.getenv("TRADING_START_HOUR", "9"))
    TRADING_END_HOUR = int(os.getenv("TRADING_END_HOUR", "15"))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    SIGNAL_CHANNEL = "trade:signals"
    ORDER_CHANNEL = "trade:orders"
