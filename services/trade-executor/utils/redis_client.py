"""
Redis Client
Handles communication between Linux Docker services and Windows VM via Redis.
Uses Proxmox bridge networking (192.168.1.x).
"""

import json
import redis
from typing import Callable, Dict, Optional, Any
from loguru import logger


class RedisClient:
    """Redis client for inter-service communication via bridge network."""

    def __init__(self, host: str = "192.168.1.100", port: int = 6379,
                 password: str = "", db: int = 0):
        """
        Initialize Redis client.
        
        Args:
            host: Redis server IP (Linux Docker host via Proxmox bridge)
            port: Redis port
            password: Redis password
            db: Redis database number
        """
        self.host = host
        self.port = port
        self.password = password
        self.db = db
        self._client: Optional[redis.Redis] = None
        self._pubsub: Optional[redis.client.PubSub] = None
        self._connect()

    def _connect(self):
        """Establish Redis connection."""
        try:
            self._client = redis.Redis(
                host=self.host,
                port=self.port,
                password=self.password if self.password else None,
                db=self.db,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            self._client.ping()
            logger.success(f"Connected to Redis at {self.host}:{self.port}")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._client = None

    def ensure_connected(self) -> bool:
        """Ensure Redis connection is active, reconnect if needed."""
        if self._client:
            try:
                self._client.ping()
                return True
            except (redis.ConnectionError, redis.TimeoutError):
                logger.warning("Redis connection lost, reconnecting...")
                self._connect()
                return self._client is not None
        else:
            self._connect()
            return self._client is not None

    def publish(self, channel: str, data: Dict) -> bool:
        """
        Publish message to Redis channel.
        
        Args:
            channel: Redis channel name
            data: Data to publish (will be JSON serialized)
        
        Returns:
            True if published successfully
        """
        if not self.ensure_connected():
            return False

        try:
            message = json.dumps(data, ensure_ascii=False, default=str)
            self._client.publish(channel, message)
            logger.debug(f"Published to {channel}: {message[:100]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to publish to {channel}: {e}")
            return False

    def subscribe(self, channel: str, callback: Callable[[Dict], None]):
        """
        Subscribe to Redis channel and process messages.
        
        Args:
            channel: Redis channel name
            callback: Function to call with each message (dict)
        """
        if not self.ensure_connected():
            logger.error("Cannot subscribe: no Redis connection")
            return

        try:
            self._pubsub = self._client.pubsub()
            self._pubsub.subscribe(channel)
            logger.info(f"Subscribed to {channel}")

            for message in self._pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        logger.info(f"Received from {channel}: {data}")
                        callback(data)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode message: {e}")
                    except Exception as e:
                        logger.error(f"Callback error: {e}")

        except Exception as e:
            logger.error(f"Subscription error: {e}")

    def get(self, key: str) -> Optional[Any]:
        """Get value from Redis."""
        if not self.ensure_connected():
            return None
        try:
            return self._client.get(key)
        except Exception as e:
            logger.error(f"Redis get error: {e}")
            return None

    def set(self, key: str, value: Any, expire: int = 3600) -> bool:
        """Set value in Redis with optional expiry."""
        if not self.ensure_connected():
            return False
        try:
            self._client.set(key, value, ex=expire)
            return True
        except Exception as e:
            logger.error(f"Redis set error: {e}")
            return False

    def close(self):
        """Close Redis connection."""
        if self._pubsub:
            self._pubsub.close()
        if self._client:
            self._client.close()
        logger.info("Redis connection closed")
