import os
import sys
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Redis settings (connects to Linux Docker via Proxmox bridge)
    REDIS_HOST = os.getenv("BRIDGE_HOST", "192.168.1.100")
    REDIS_PORT = int(os.getenv("BRIDGE_PORT", "6379"))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

    # PostgreSQL (via bridge)
    POSTGRES_HOST = os.getenv("PG_HOST", "192.168.1.100")
    POSTGRES_PORT = int(os.getenv("PG_PORT", "5432"))
    POSTGRES_DB = os.getenv("POSTGRES_DB", "stock_trading")
    POSTGRES_USER = os.getenv("POSTGRES_USER", "stock_user")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

    # Creon API
    CREON_USER_ID = os.getenv("CREON_USER_ID", "")
    CREON_PASSWORD = os.getenv("CREON_PASSWORD", "")
    CREON_CERT_PASSWORD = os.getenv("CREON_CERT_PASSWORD", "")
    CREON_ACCOUNT = os.getenv("CREON_ACCOUNT", "")

    # Trading settings
    TRADING_START_HOUR = int(os.getenv("TRADING_START_HOUR", "9"))
    TRADING_END_HOUR = int(os.getenv("TRADING_END_HOUR", "15"))
    MAX_POSITION_SIZE = int(os.getenv("MAX_POSITION_SIZE", "10000000"))
    MAX_DAILY_TRADE = int(os.getenv("MAX_DAILY_TRADE", "50000000"))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # Signal channels
    SIGNAL_CHANNEL = "trade:signals"
    ORDER_CHANNEL = "trade:orders"
    STATUS_CHANNEL = "trade:status"
