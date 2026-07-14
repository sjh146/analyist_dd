"""
Redis Storage for Strategy Agents
Publishes trade signals to Redis Streams.
"""

import redis
import json
import logging
from typing import Dict, Optional

from app.config import Config

logger = logging.getLogger(__name__)

try:
    from services.shared.redis_streams import RedisStreams
except ImportError:
    RedisStreams = None  # type: ignore


class RedisStorage:
    STREAM_NAME = "strategy:signals"

    def __init__(self):
        self.config = Config()
        self._client = None
        self._streams: Optional[RedisStreams] = None
        self._connect()

    def _connect(self):
        try:
            self._client = redis.Redis(
                host=self.config.REDIS_HOST,
                port=self.config.REDIS_PORT,
                password=self.config.REDIS_PASSWORD if self.config.REDIS_PASSWORD else None,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            self._client.ping()
            logger.info("Connected to Redis for strategy agents")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            return

        if RedisStreams:
            try:
                redis_url = f"redis://:{self.config.REDIS_PASSWORD or ''}@{self.config.REDIS_HOST}:{self.config.REDIS_PORT}"
                self._streams = RedisStreams(redis_url=redis_url)
                self._streams.create_group(self.STREAM_NAME, "trade-executor", mkstream=True)
            except Exception as e:
                logger.warning("RedisStreams init failed: %s", e)
                self._streams = None

    def publish_signal(self, signal: Dict) -> bool:
        """Publish trade signal to Redis Streams."""
        if not self._client:
            return False

        signal_data = {
            "strategy_name": signal.get("strategy_name", "unknown"),
            "stock_code": signal.get("stock_code", signal.get("ticker", "unknown")),
            "signal": signal.get("signal", ""),
            "confidence": str(signal.get("confidence", 0.0)),
            "timestamp": signal.get("timestamp", ""),
        }

        if self._streams is None:
            logger.error("Redis Streams not available; cannot publish signal")
            return False

        try:
            self._streams.xadd(self.STREAM_NAME, signal_data, maxlen=10000)
            logger.info(
                "Published signal to stream %s for %s/%s",
                self.STREAM_NAME,
                signal_data["strategy_name"],
                signal_data["stock_code"],
            )
            return True
        except Exception as e:
            logger.error(f"Failed to publish signal to stream: {e}")
            return False

    def get_pending_orders(self) -> list:
        """Get pending orders from Redis."""
        if not self._client:
            return []
        try:
            data = self._client.get("pending_orders")
            return json.loads(data) if data else []
        except Exception:
            return []

    def close(self):
        if self._client:
            self._client.close()
