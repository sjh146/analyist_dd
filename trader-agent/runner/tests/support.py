"""Shared helpers for runner tests (fixtures, fake feed clients)."""

from __future__ import annotations

import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from trader_core import Config, FeedError, FeedSnapshot
from trader_core.config import KST

from trader_core.tests import support as tc_support

from runner.config import RunnerConfig

MONDAY_OPEN = datetime(2026, 9, 7, 9, 5, tzinfo=KST)  # Monday 2026-09-07
MONDAY_CLOSE_DONE = datetime(2026, 9, 7, 15, 45, tzinfo=KST)
TUESDAY_OPEN = datetime(2026, 9, 8, 9, 5, tzinfo=KST)
FIVE_MIN = timedelta(minutes=5)

DEFAULT_PRICE = 10000.0
DEFAULT_SCORE = "90.0"


def make_snapshot(
    codes: List[str],
    generated: str = "2026-09-07T08:55:00+09:00",
    score: str = DEFAULT_SCORE,
    close_price: str = str(DEFAULT_PRICE),
) -> FeedSnapshot:
    """Build a feed snapshot whose ``close`` list carries one item per *codes*."""
    items = [
        tc_support.make_candidate(
            rank=i + 1, code=code, close_price=close_price, score=score
        )
        for i, code in enumerate(codes)
    ]
    payload = tc_support.make_payload(
        generated_at=generated,
        close=items,
        swing=None,
        daytrading=None,
        screener_stats=None,
    )
    return FeedSnapshot.from_payload(payload)


def quote_map(codes: List[str], price: float = DEFAULT_PRICE) -> Dict[str, float]:
    """Return ``{code: price}`` quote seeds for *codes*."""
    return {code: price for code in codes}


class StaticFeed:
    """A feed client that always returns the same snapshot."""

    def __init__(self, snapshot: FeedSnapshot) -> None:
        self.snapshot = snapshot

    def fetch(self) -> FeedSnapshot:
        return self.snapshot


class SequenceFeed:
    """A feed client that hands out a queue of snapshots, then *error*.

    Useful for day-rollover and check-failure scenarios.
    """

    def __init__(
        self,
        snapshots: List[FeedSnapshot],
        error: Optional[Exception] = None,
    ) -> None:
        self._queue = deque(snapshots)
        self.error = error

    def fetch(self) -> FeedSnapshot:
        if self.error is not None:
            raise self.error
        if self._queue:
            return self._queue.popleft()
        raise FeedError("feed exhausted")


class ErrorFeed:
    """A feed client whose fetch always raises *error*."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def fetch(self) -> FeedSnapshot:
        raise self.error


def make_loop_config(
    tmp: str,
    *,
    max_consecutive: int = 3,
    r1_threshold: Optional[float] = None,
    r2_max: int = 3,
    **runner_fields: Any,
) -> RunnerConfig:
    """Build a runner config pointing every file into a temp dir."""
    engine = Config(
        market_hours_only=True,
        feed_max_age=30 * 3600.0,  # snapshots stay fresh through a session
        kill_switch_path=os.path.join(tmp, "kill_switch.txt"),
        # Unit runs must not depend on the network; the clock guard is covered
        # explicitly by trader_core/tests/test_clockcheck.py.
        clock_check_url="",
    )
    overrides: Dict[str, Any] = dict(runner_fields)
    overrides.setdefault("state_path", os.path.join(tmp, "loop_state.json"))
    overrides.setdefault("journal_path", os.path.join(tmp, "journal.sqlite3"))
    # Tests never hit the real BC-250 news gate (and unit runs must not
    # depend on the network); R5 gate tests opt in via their own URL.
    overrides.setdefault("news_gate_url", "")
    overrides.setdefault("max_consecutive_check_failures", max_consecutive)
    overrides.setdefault("r2_max_entries_per_day", r2_max)
    # The generic fixtures trade a ``close`` list at 09:05, which the real
    # 종가 profile (entry window 14:50-15:25) correctly refuses.  Tests that are
    # not about the window itself get a permissive window here; the window rules
    # are pinned by test_profiles.py and test_entry_windows.py.
    overrides.setdefault(
        "screener_profiles",
        {
            "close": {"entry_window": "09:00-15:20", "exit_mode": "stop_tp"},
            "swing": {"entry_window": "09:00-15:20", "exit_mode": "stop_tp"},
        },
    )
    if r1_threshold is not None:
        overrides["r1_min_avg_score"] = {"close": r1_threshold, "swing": 75.0}
    cfg = RunnerConfig(engine=engine, **overrides)
    return cfg


def make_loop_bridge_and_feed(
    tmp: str,
    feed: Any,
    *,
    start_cash: float = 10_000_000.0,
    codes: Optional[List[str]] = None,
    log: Optional[Any] = None,
    **runner_fields: Any,
) -> tuple:
    """Return ``(cfg, loop, mock)`` wired to a temp dir.

    A ``log`` callable may be supplied to silence/capture loop + notify lines.
    """
    from trader_core import MockBridge

    from runner.loop import MarketLoop

    cfg = make_loop_config(tmp, **runner_fields)
    mock = MockBridge(start_cash=start_cash, quotes=quote_map(codes or []))
    loop = MarketLoop(mock, feed, cfg, log=log)
    return cfg, loop, mock


def read_state(tmp: str) -> Dict[str, Any]:
    from runner.state import StateStore

    cfg = make_loop_config(tmp)
    return StateStore(cfg.state_path).read()
