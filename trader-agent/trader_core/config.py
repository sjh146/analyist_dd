"""Runtime configuration for trader-core.

All defaults come from PHASE3_BRIEF.md.  A :class:`Config` can be built
directly, layered from a JSON file (``--config``), from environment
variables (``TRADER_CORE_<FIELD>``) or from CLI flags.  CLI > env > file >
defaults.  ``trader_core`` is stdlib-only and timezone aware: the reference
clock is KST (UTC+9) because both the bridge account and the BC-250 feed
live in Korea.
"""

from __future__ import annotations

import json
import os
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


KST = timezone(timedelta(hours=9))

_ENV_PREFIX = "TRADER_CORE_"


DEFAULT_JOURNAL_PATH = os.path.join("journal", "trade_journal.sqlite3")


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    return default


def _as_strlist(value: Any, default: List[str]) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return default


@dataclass
class Config:
    """Trader-core configuration (risk, feed, bridge, market-hours, journal)."""


    bridge_base_url: str = "http://127.0.0.1:8100"
    feed_url: str = "http://127.0.0.1:8090/screener_latest.json"







    feed_max_age: float = 8.0 * 3600.0
    request_timeout: float = 5.0





    feed_source: str = "url"
    rank_screener: str = "rank_momentum"
    rank_limit: int = 60







    rank_pct_to: int = 15
    rank_sort: int = 21


    rank_params_path: str = "strategy_params.json"




    market_hours_only: bool = True
    market_open: str = "09:00"
    market_close: str = "15:30"


    daytrading_requires_market_open: bool = True


    stop_loss_pct: float = 0.07
    take_profit_pct: float = 0.07

























    trailing_stop_pct: float = 0.05
    trailing_arm_pct: float = 0.055
    kelly_cap: float = 0.25







    kelly_fraction: float = 1.0






    default_win_rate: float = 0.53
    default_avg_win: float = 1.0
    default_avg_loss: float = 1.0




    daily_max_trades: int = 3





    max_position_pct: float = 1.0 / 3.0


    sizing_mode: str = "equal_weight"

    max_order_amount: float = 10_000_000.0
    market_order_band_pct: float = 0.10









    confirm_entry_fills: bool = True











    max_open_positions: int = 3










    max_open_positions_by_screener: dict = field(
        default_factory=lambda: {"swing": 2, "daytrading": 2}
    )
    max_invested_pct: float = 1.0













    post_exit_cooldown_min: float = 30.0
    post_exit_cooldown_scope: str = "screener"























    min_edge_score: int = 4



    min_rr: float = 0.0
    edge_gate_fail_open: bool = True




    stop_range_factor: float = 0.5
    max_stop_pct: float = 0.20
    risk_per_trade_pct: float = 0.005





    quote_divergence_pct: float = 0.01


    clock_check_url: str = "https://www.google.com"
    clock_max_skew_seconds: float = 300.0
    clock_check_interval_seconds: float = 600.0
    clock_check_timeout_seconds: float = 8.0


    clock_check_fail_closed: bool = False


    min_score: float = 0.0
    order_type: str = "limit"


    screener_names: List[str] = field(
        default_factory=lambda: ["close", "swing", "daytrading"]
    )


    kill_switch_path: str = "kill_switch.txt"
    journal_path: str = DEFAULT_JOURNAL_PATH
    dry_run: bool = True




    @classmethod
    def env_overrides(cls, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Return raw ``TRADER_CORE_*`` env vars keyed by field name.

        Only variables that are actually present are returned (so callers can
        layer env over file/CLI without clobbering unset fields).
        """
        source = os.environ if env is None else env
        result: Dict[str, Any] = {}
        for key, value in source.items():
            if not key.startswith(_ENV_PREFIX):
                continue
            name = key[len(_ENV_PREFIX):].lower()
            if name in _FIELD_NAMES:
                result[name] = value
        return result

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "Config":
        """Build a :class:`Config` honouring ``TRADER_CORE_*`` environment vars.

        ``env`` may be injected for tests (defaults to ``os.environ``).
        Unknown/ill-typed values fall back to the dataclass defaults.
        """
        return cls._coerce(cls.env_overrides(env))

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Build a :class:`Config` from a JSON file (subset of fields)."""
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("config file must contain a JSON object")
        return cls._coerce({k.lower(): v for k, v in raw.items()})

    @classmethod
    def _coerce(cls, data: Dict[str, Any]) -> "Config":
        cfg = cls()
        for name, value in data.items():
            if name not in _FIELD_NAMES:
                continue
            current = getattr(cfg, name)
            if name == "screener_names":
                coerced = _as_strlist(value, current)  # type: ignore[arg-type]
            elif isinstance(current, dict):



                if isinstance(value, dict):
                    coerced = {str(k): v for k, v in value.items()}
                elif isinstance(value, str):
                    try:
                        parsed = json.loads(value)
                    except ValueError:
                        parsed = None
                    coerced = parsed if isinstance(parsed, dict) else current
                else:
                    coerced = current
            elif isinstance(current, bool):
                coerced = _as_bool(value, current)
            elif isinstance(current, int):
                coerced = _as_int(value, current)
            elif isinstance(current, float):
                coerced = _as_float(value, current)
            else:
                coerced = str(value)
            setattr(cfg, name, coerced)
        return cfg

    def with_overrides(self, **overrides: Any) -> "Config":
        """Return a copy of this config with ``overrides`` applied."""
        return Config._coerce({**self.as_dict(), **overrides})

    def as_dict(self) -> Dict[str, Any]:
        """Return the config as a plain dict (useful for JSON output).

        Every dataclass field is included — ``_FIELD_NAMES`` (the set of names
        that ``from_env`` / ``from_file`` will accept) is derived from this
        dict, so a field missing here is silently *not* settable from a config
        file or an environment variable.  Risk knobs such as
        ``max_invested_pct`` / ``max_open_positions`` used to be omitted and
        were therefore ignored by ``--config`` — a portability trap: a new
        operator could not lower the exposure limits at all.
        """
        result: Dict[str, Any] = {}
        for item in dataclasses.fields(self):
            value = getattr(self, item.name)
            if isinstance(value, list):
                result[item.name] = list(value)
            elif isinstance(value, dict):
                result[item.name] = dict(value)
            else:
                result[item.name] = value
        return result

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Config({0})".format(
            ", ".join("{0}={1!r}".format(k, v) for k, v in self.as_dict().items())
        )


_FIELD_NAMES = frozenset(Config().as_dict().keys())


def now_kst() -> datetime:
    """Return the current time as a timezone-aware KST datetime."""
    return datetime.now(KST)
