"""
Redis Storage for Strategy Agents
Publishes trade signals to the Redis channel for Windows VM executor.
"""

import redis
import json
import logging
from typing import Dict, Optional

from app.config import Config

logger = logging.getLogger(__name__)


class RedisStorage:
    def __init__(self):
        self.config = Config()
        self._client = None
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

    def publish_signal(self, signal: Dict) -> bool:
        """Publish trade signal to Redis."""
        if not self._client:
            return False
        try:
            message = json.dumps(signal, ensure_ascii=False, default=str)
            self._client.publish(self.config.SIGNAL_CHANNEL, message)
            logger.info(f"Published signal to {self.config.SIGNAL_CHANNEL}")
            return True
        except Exception as e:
            logger.error(f"Failed to publish signal: {e}")
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
