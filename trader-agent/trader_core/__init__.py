"""trader-core: 64-bit decision/risk engine for the Creon trader.

Library + engine layer that sources screener signals (BC-250 feed), sizes and
gates orders (fractional Kelly + balance/position/market-order guards),
watches open positions for stop/take-profit exits, and journals everything to
SQLite.  It NEVER touches Creon COM directly; it talks only to the local
creon-bridge HTTP API (``bridge/``) and the BC-250 feed over HTTP.

Stdlib only: ``urllib``, ``http``, ``sqlite3``, ``datetime``, ``dataclasses``.
"""

from __future__ import annotations

from .bridge_client import (
    BridgeClient,
    BridgeError,
    BridgeResponseError,
    BridgeTransportError,
)
from .config import Config, KST, now_kst
from .engine import (
    AccountState,
    Decision,
    DecisionReport,
    GateResult,
    OrderBlockedError,
    OrderPlan,
    TraderEngine,
    market_is_open,
)
from .feed import (
    Candidate,
    FeedError,
    FeedSnapshot,
    ScreenerFeedClient,
    normalize_code,
)
from .journal import Journal
from .killswitch import kill_switch_active, kill_switch_reason
from .mockbridge import MockBridge
from .monitor import ExitSignal, MonitorReport, PositionMonitor
from .tickwatch import TickBreach, tick_exit_candidates, tick_age
from . import risk

__version__ = "1.0.0"

__all__ = [
    "AccountState",
    "BridgeClient",
    "BridgeError",
    "BridgeResponseError",
    "BridgeTransportError",
    "Candidate",
    "Config",
    "Decision",
    "DecisionReport",
    "ExitSignal",
    "TickBreach",
    "FeedError",
    "FeedSnapshot",
    "GateResult",
    "Journal",
    "KST",
    "MockBridge",
    "MonitorReport",
    "OrderBlockedError",
    "OrderPlan",
    "PositionMonitor",
    "ScreenerFeedClient",
    "TraderEngine",
    "kill_switch_active",
    "kill_switch_reason",
    "market_is_open",
    "normalize_code",
    "now_kst",
    "risk",
]
