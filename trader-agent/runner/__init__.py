"""runner: the market-hours operating loop for the sim-account trader.

Phase 4a package: orchestrates the existing trader-core engine/monitor against
the local creon bridge during KST market hours, applying the deterministic
R1-R4 rules on top, always journaling, and persisting its HALT flag + daily
counters to ``loop_state.json`` so remote ops can steer it.

Stdlib only.  Import trader-core wholesale; never modify it.
"""

from .config import RunnerConfig
from .loop import HALT_REASON_LOSS, LoopReport, MarketLoop
from .notify import Notifier, build_payload
from .rules import (
    apply_r1,
    apply_r2,
    r3_deviation_ok,
    r3_reason,
    r4_triggered,
    top_n_average,
)
from .state import StateStore, default_state

__version__ = "1.0.0"

__all__ = [
    "HALT_REASON_LOSS",
    "LoopReport",
    "MarketLoop",
    "Notifier",
    "RunnerConfig",
    "StateStore",
    "apply_r1",
    "apply_r2",
    "build_payload",
    "default_state",
    "r3_deviation_ok",
    "r3_reason",
    "r4_triggered",
    "top_n_average",
]
