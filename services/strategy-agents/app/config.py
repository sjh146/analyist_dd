import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "")
    POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", ""))
    POSTGRES_DB = os.getenv("POSTGRES_DB", "")
    POSTGRES_USER = os.getenv("POSTGRES_USER", "")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

    REDIS_HOST = os.getenv("REDIS_HOST", "")
    REDIS_PORT = int(os.getenv("REDIS_PORT", ""))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

    BRIDGE_VM_IP = os.getenv("BRIDGE_VM_IP", "")
    BRIDGE_VM_PORT = int(os.getenv("BRIDGE_VM_PORT", ""))

    TRADING_START_HOUR = int(os.getenv("TRADING_START_HOUR", ""))
    TRADING_END_HOUR = int(os.getenv("TRADING_END_HOUR", ""))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "")
    SIGNAL_CHANNEL = "trade:signals"
    ORDER_CHANNEL = "trade:orders"
