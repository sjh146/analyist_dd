import os
import sys
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Redis settings (connects to Linux Docker via Proxmox bridge)
    REDIS_HOST = os.getenv("BRIDGE_HOST", "")
    REDIS_PORT = int(os.getenv("BRIDGE_PORT", ""))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

    # PostgreSQL (via bridge)
    POSTGRES_HOST = os.getenv("PG_HOST", "")
    POSTGRES_PORT = int(os.getenv("PG_PORT", ""))
    POSTGRES_DB = os.getenv("POSTGRES_DB", "")
    POSTGRES_USER = os.getenv("POSTGRES_USER", "")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

    # Creon API
    CREON_USER_ID = os.getenv("CREON_USER_ID", "")
    CREON_PASSWORD = os.getenv("CREON_PASSWORD", "")
    CREON_CERT_PASSWORD = os.getenv("CREON_CERT_PASSWORD", "")
    CREON_ACCOUNT = os.getenv("CREON_ACCOUNT", "")

    # Trading settings
    TRADING_START_HOUR = int(os.getenv("TRADING_START_HOUR", ""))
    TRADING_END_HOUR = int(os.getenv("TRADING_END_HOUR", ""))
    MAX_POSITION_SIZE = int(os.getenv("MAX_POSITION_SIZE", ""))
    MAX_DAILY_TRADE = int(os.getenv("MAX_DAILY_TRADE", ""))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "")

    # Signal channels
    SIGNAL_CHANNEL = "trade:signals"
    ORDER_CHANNEL = "trade:orders"
    STATUS_CHANNEL = "trade:status"
