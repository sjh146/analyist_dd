import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# 장 마감(15:30) 여유 시각. 이 시각 이전 수집분은 당일 봉이 미완성이므로 저장/요청에서 제외한다.
MARKET_CLOSE_BUFFER_HOUR = int(os.getenv("MARKET_CLOSE_BUFFER_HOUR", "15"))
MARKET_CLOSE_BUFFER_MINUTE = int(os.getenv("MARKET_CLOSE_BUFFER_MINUTE", "40"))

try:  # tzdata 없는 slim 이미지 대비
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover
    KST = timezone(timedelta(hours=9))


def kst_now() -> datetime:
    """컨테이너 TZ(주로 UTC)와 무관한 한국 시각."""
    return datetime.now(KST)


def kst_market_closed() -> bool:
    """한국 장 마감(+여유) 이후인가."""
    now = kst_now()
    return (now.hour, now.minute) >= (MARKET_CLOSE_BUFFER_HOUR, MARKET_CLOSE_BUFFER_MINUTE)


class Config:
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB = os.getenv("POSTGRES_DB", "stock_trading")
    POSTGRES_USER = os.getenv("POSTGRES_USER", "stock_user")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

    COLLECTION_INTERVAL_HOURS = int(os.getenv("COLLECTION_INTERVAL_HOURS", "24"))
    DEFAULT_PERIOD = "1y"

    @classmethod
    def get_pg_dsn(cls) -> str:
        return (
            f"host={cls.POSTGRES_HOST} port={cls.POSTGRES_PORT} "
            f"dbname={cls.POSTGRES_DB} user={cls.POSTGRES_USER} "
            f"password={cls.POSTGRES_PASSWORD}"
        )
