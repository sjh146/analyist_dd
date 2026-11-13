"""Bridge runtime configuration.

Defaults match the PHASE2 brief: 127.0.0.1:8100.  Everything can be
overridden through environment variables or by constructing a :class:`Config`
directly (the CLI in ``run_bridge.py`` layers argparse on top).
"""

from __future__ import annotations

import os
from typing import Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100

_ENV_HOST = "CREON_BRIDGE_HOST"
_ENV_PORT = "CREON_BRIDGE_PORT"
_ENV_REQUEST_TIMEOUT = "CREON_BRIDGE_REQUEST_TIMEOUT"

DEFAULT_REQUEST_TIMEOUT = 30.0  # seconds a single blocking request may take
DEFAULT_BLOCK_MAX_RETRIES = 6    # retries after Cybos "4" (continuous comms limit)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class Config:
    """Configuration container for the bridge service."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        block_max_retries: int = DEFAULT_BLOCK_MAX_RETRIES,
    ) -> None:
        self.host = host
        self.port = port
        self.request_timeout = request_timeout
        self.block_max_retries = block_max_retries

    @classmethod
    def from_env(cls) -> "Config":
        """Build a :class:`Config` honouring ``CREON_BRIDGE_*`` env vars."""
        return cls(
            host=os.environ.get(_ENV_HOST, DEFAULT_HOST),
            port=_env_int(_ENV_PORT, DEFAULT_PORT),
            request_timeout=_env_float(_ENV_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
        )

    def with_overrides(
        self, host: Optional[str] = None, port: Optional[int] = None
    ) -> "Config":
        """Return a copy with any provided overrides applied."""
        return Config(
            host=host if host else self.host,
            port=port if port is not None else self.port,
            request_timeout=self.request_timeout,
            block_max_retries=self.block_max_retries,
        )

    def endpoint_url(self) -> str:
        """Return the printable local endpoint URL."""
        return "http://{0}:{1}".format(self.host, self.port)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            "Config(host={0.host!r}, port={0.port!r}, "
            "request_timeout={0.request_timeout!r}, "
            "block_max_retries={0.block_max_retries!r})".format(self)
        )
