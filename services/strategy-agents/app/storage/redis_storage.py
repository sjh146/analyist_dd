"""
Redis Storage for Strategy Agents
Publishes trade signals to Redis Streams.
"""

import os
import hmac
import hashlib
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


def _sign_signal(data: dict) -> dict:
    """TRADE_SIGNAL_SECRET로 HMAC-SHA256 서명 추가 (CWE-306 방지 — 무인증 신호 주입 차단).

    canonical: 정렬된 key=value 를 '&'로 결합해 HMAC. 비밀이 없으면 서명 없이 반환
    (로컬 개발) — 운영은 반드시 TRADE_SIGNAL_SECRET 설정.
    """
    secret = os.environ.get("TRADE_SIGNAL_SECRET", "")
    if not secret:
        return data
    canonical = "&".join(f"{k}={v}" for k, v in sorted(data.items()))
    sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    out = dict(data)
    out["sig"] = sig
    return out


def verify_signal_signature(data: dict) -> bool:
    """TRADE_SIGNAL_SECRET로 서명 검증 (trade-executor가 사용).

    서명이 없거나 불일치하면 False — 무인증 신호 주입(trade:signals XADD)을 차단한다.
    비밀 미설정 시 **거부(fail-closed)** — 기본 배포에서 제어가 no-op이 되는
    CWE-306 경로를 차단한다. 로컬 개발도 TRADE_SIGNAL_SECRET을 설정해야 한다.
    """
    secret = os.environ.get("TRADE_SIGNAL_SECRET", "")
    if not secret:
        return False  # fail-closed: 시크릿 없으면 모든 신호 거부
    sig = data.pop("sig", None) if isinstance(data, dict) else None
    if not sig:
        return False
    canonical = "&".join(f"{k}={v}" for k, v in sorted(data.items()))
    expected = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


class RedisStorage:
    STREAM_NAME = "strategy:signals"
    PAPER_STREAM_NAME = "paper:factor_signals"
    PAPER_ACKMAN_STREAM_NAME = "paper:ackman_signals"

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
                self._streams.create_group(self.PAPER_STREAM_NAME, "paper-monitor", mkstream=True)
                self._streams.create_group(self.PAPER_ACKMAN_STREAM_NAME, "paper-monitor", mkstream=True)
            except Exception as e:
                logger.warning("RedisStreams init failed: %s", e)
                self._streams = None

    def publish_signal(self, signal: Dict) -> bool:
        """Publish trade signal to Redis Streams (HMAC 서명 포함)."""
        if not self._client:
            return False

        signal_data = {
            "strategy_name": signal.get("strategy_name", "unknown"),
            "stock_code": signal.get("stock_code", signal.get("ticker", "unknown")),
            "signal": signal.get("signal", ""),
            "confidence": str(signal.get("confidence", 0.0)),
            "timestamp": signal.get("timestamp", ""),
        }
        signal_data = _sign_signal(signal_data)

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

    def publish_paper_signal(self, signal: Dict) -> bool:
        """Publish factor-strategy signal to the paper-only stream (never trade:signals)."""
        if not self._client:
            return False

        signal_data = {
            "strategy_name": signal.get("strategy_name", "unknown"),
            "stock_code": signal.get("stock_code", signal.get("ticker", "unknown")),
            "action": signal.get("action", signal.get("signal", "")),
            "confidence": str(signal.get("confidence", 0.0)),
            "timestamp": signal.get("timestamp", ""),
        }
        signal_data = _sign_signal(signal_data)

        if self._streams is None:
            logger.error("Redis Streams not available; cannot publish paper signal")
            return False

        try:
            self._streams.xadd(self.PAPER_STREAM_NAME, signal_data, maxlen=10000)
            logger.info(
                "Published paper signal to stream %s for %s/%s",
                self.PAPER_STREAM_NAME,
                signal_data["strategy_name"],
                signal_data["stock_code"],
            )
            return True
        except Exception as e:
            logger.error(f"Failed to publish paper signal to stream: {e}")
            return False

    def publish_ackman_signal(self, signal: Dict) -> bool:
        """Publish ackman (thesis) strategy signal to the paper-only stream (never trade:signals)."""
        if not self._client:
            return False

        signal_data = {
            "strategy_name": signal.get("strategy_name", "unknown"),
            "stock_code": signal.get("stock_code", signal.get("ticker", "unknown")),
            "action": signal.get("action", signal.get("signal", "")),
            "confidence": str(signal.get("confidence", 0.0)),
            "timestamp": signal.get("timestamp", ""),
            "thesis_id": signal.get("thesis_id", ""),
            "position_size_pct": signal.get("position_size_pct", ""),
        }
        signal_data = _sign_signal(signal_data)

        if self._streams is None:
            logger.error("Redis Streams not available; cannot publish ackman signal")
            return False

        try:
            self._streams.xadd(self.PAPER_ACKMAN_STREAM_NAME, signal_data, maxlen=10000)
            logger.info(
                "Published ackman signal to stream %s for %s/%s",
                self.PAPER_ACKMAN_STREAM_NAME,
                signal_data["strategy_name"],
                signal_data["stock_code"],
            )
            return True
        except Exception as e:
            logger.error(f"Failed to publish ackman signal to stream: {e}")
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
