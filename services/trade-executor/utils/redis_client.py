"""
Redis Client
Handles communication between Linux Docker services and Windows VM via Redis.
Uses Proxmox bridge networking (192.168.1.x).
"""

import json
import socket
import time
import redis
from typing import Callable, Dict, Optional, Any
from loguru import logger
from services.shared.redis_streams import RedisStreams


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

    def subscribe_streams(self, callback: Callable[[Dict], None]):
        """
        Subscribe to Redis Streams using Consumer Groups (persistent consumption with ACK).

        Args:
            callback: Function to call with each message (dict)
        """
        if not self.ensure_connected():
            logger.error("Cannot subscribe to streams: no Redis connection")
            return

        hostname = socket.gethostname()
        consumer_name = f"trade-executor-{hostname}"

        redis_url = f"redis://{self.host}:{self.port}"
        if self.password:
            redis_url = f"redis://:{self.password}@{self.host}:{self.port}"

        streams_client = RedisStreams(redis_url)
        if not streams_client._client:
            raise ConnectionError("RedisStreams connection failed")

        stream_groups = {
            "trading:signals": "trading:signals",
            "strategy:signals": "strategy:signals",
        }

        for stream, group in stream_groups.items():
            streams_client.create_group(stream, group)

        logger.info(f"Subscribed to Redis Streams as consumer '{consumer_name}'")
        logger.info(f"Monitoring: {', '.join(stream_groups.keys())}")

        last_pending_check = time.time()
        pending_interval = 30

        while True:
            try:
                if not self.ensure_connected():
                    time.sleep(5)
                    continue

                for stream, group in stream_groups.items():
                    results = streams_client.xreadgroup(
                        group=group,
                        consumer=consumer_name,
                        streams={stream: ">"},
                        block=2000,
                    )

                    for stream_name, messages in results:
                        for msg_id, msg_data in messages:
                            try:
                                data_str = msg_data.get("data", "{}")
                                if isinstance(data_str, bytes):
                                    data_str = data_str.decode()
                                data = json.loads(data_str)
                                logger.info(f"Received from {stream_name}: {data}")
                                callback(data)
                            except json.JSONDecodeError as e:
                                logger.error(f"Failed to decode from {stream_name}: {e}")
                            except Exception as e:
                                logger.error(f"Callback error on {stream_name}: {e}")
                            finally:
                                streams_client.xack(stream, group, msg_id)

                now = time.time()
                if now - last_pending_check >= pending_interval:
                    self._process_pending(streams_client, consumer_name, stream_groups, callback)
                    last_pending_check = now

            except Exception as e:
                logger.error(f"Stream subscription error: {e}")
                time.sleep(5)

    def _process_pending(
        self,
        streams_client: RedisStreams,
        consumer_name: str,
        stream_groups: dict,
        callback: Callable[[Dict], None],
    ):
        for stream, group in stream_groups.items():
            try:
                pending_list = streams_client._client.xpending(
                    stream, group, consumer=consumer_name, idle=60000
                )
                if not pending_list:
                    continue

                logger.info(f"Found {len(pending_list)} pending on {stream}")
                for entry in pending_list:
                    msg_id = entry[0]
                    entries = streams_client._client.xrange(stream, min=msg_id, max=msg_id, count=1)
                    if entries:
                        _, msg_data = entries[0]
                        try:
                            data_str = msg_data.get("data", "{}")
                            if isinstance(data_str, bytes):
                                data_str = data_str.decode()
                            data = json.loads(data_str)
                            logger.info(f"Re-processing pending from {stream}: {data}")
                            callback(data)
                        except Exception as e:
                            logger.warning(f"Pending message error on {stream}: {e}")
                        finally:
                            streams_client.xack(stream, group, msg_id)
            except Exception as e:
                logger.warning(f"Pending check failed for {stream}: {e}")

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
        if self._client:
            self._client.close()
        logger.info("Redis connection closed")
