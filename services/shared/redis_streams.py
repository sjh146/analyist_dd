import json
import logging
from typing import Any, Optional

import redis

logger = logging.getLogger(__name__)


class RedisStreams:
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._pool: Optional[redis.ConnectionPool] = None
        self._client: Optional[redis.Redis] = None
        try:
            self._pool = redis.ConnectionPool.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
            )
            self._client = redis.Redis(connection_pool=self._pool)
            self._client.ping()
            logger.info("RedisStreams connected to %s", redis_url)
        except Exception as e:
            logger.error("RedisStreams connection failed: %s", e)

    def xadd(self, stream: str, data: dict, maxlen: int = 10000) -> str:
        if not self._client:
            logger.warning("Redis not available; xadd skipped")
            return ""
        try:
            return self._client.xadd(stream, data, maxlen=maxlen, approximate=False)
        except Exception as e:
            logger.error("xadd to %s failed: %s", stream, e)
            return ""

    def xread(self, streams: dict, block: int = 5000) -> list:
        if not self._client:
            logger.warning("Redis not available; xread skipped")
            return []
        try:
            result = self._client.xread(streams, block=block, count=None)
            return result if result else []
        except Exception as e:
            logger.error("xread failed: %s", e)
            return []

    def xreadgroup(self, group: str, consumer: str, streams: dict, block: int = 5000) -> list:
        if not self._client:
            logger.warning("Redis not available; xreadgroup skipped")
            return []
        try:
            result = self._client.xreadgroup(group, consumer, streams, block=block, count=None)
            return result if result else []
        except Exception as e:
            logger.error("xreadgroup failed: %s", e)
            return []

    def xack(self, stream: str, group: str, *message_ids) -> int:
        if not self._client:
            logger.warning("Redis not available; xack skipped")
            return 0
        try:
            return self._client.xack(stream, group, *message_ids)
        except Exception as e:
            logger.error("xack failed for %s/%s: %s", stream, group, e)
            return 0

    def create_group(self, stream: str, group: str, mkstream: bool = True) -> bool:
        if not self._client:
            logger.warning("Redis not available; create_group skipped")
            return False
        try:
            self._client.xgroup_create(stream, group, id="$", mkstream=mkstream)
            logger.info("Consumer group %s created on %s", group, stream)
            return True
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info("Consumer group %s already exists on %s", group, stream)
                return True
            logger.error("create_group failed: %s", e)
            return False
        except Exception as e:
            logger.error("create_group failed: %s", e)
            return False

    def close(self):
        if self._client:
            self._client.close()
        if self._pool:
            self._pool.disconnect()
