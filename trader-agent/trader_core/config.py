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

# KST is the canonical clock for market hours and journal day boundaries.
KST = timezone(timedelta(hours=9))

_ENV_PREFIX = "TRADER_CORE_"

# Default journal location is relative to the repo root (where the CLI runs).
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

    # --- bridge / feed connectivity ----------------------------------- #
    bridge_base_url: str = "http://127.0.0.1:8100"
    feed_url: str = "http://127.0.0.1:8090/screener_latest.json"
    # Screener exports land daily ~19:30 KST (evening pipeline).  The next
    # market session opens ~09:00 (age ~13.5h), so the default must cover a
    # full day; 24h keeps the previous session's signal list usable through
    # the following morning while still rejecting weekend-old payloads.
    feed_max_age: float = 24.0 * 3600.0  # seconds; default 24h
    request_timeout: float = 5.0  # seconds per outbound HTTP request

    # --- candidate source --------------------------------------------- #
    # "url"  = the external BC-250 screener JSON (feed_url above).
    # "rank" = self-generated from the broker's ranking via the bridge
    #          (trader_core/rankfeed.py) -- no external feed needed.
    feed_source: str = "url"
    rank_screener: str = "rank_momentum"  # screener key the rank feed emits
    rank_limit: int = 60     # ranking rows to collect per cycle (1-3 pages)
    # Ranking window upper bound (등락률, %).  The rank page is sorted by
    # 상승률 descending, so a 30 % window fills the head of the page with
    # +18..30 % names and the feed's chase ceiling then rejects every one of
    # them -- measured 2026-09-11: top-6 of the page = +18..30 %, 0 candidates
    # for the whole morning.  Matching the window to the ceiling
    # (rank_pct_to == max_pct) puts the shortlist inside the band that is
    # actually buyable: pct_to=15 opened at +14.5 %, pct_to=12 at +11.9 %.
    rank_pct_to: int = 15
    rank_sort: int = 21      # 정렬키: 21 = 전일대비 상위
    # Self-tuning file written by tools/review.py (filters, scoring refs and
    # Kelly priors read from the trade journal).  Missing file = defaults.
    rank_params_path: str = "strategy_params.json"

    # --- market hours (KST) ------------------------------------------ #
    # Gate on market hours by default; set to False to allow scans outside
    # the KST session.  The window is Mon-Fri 09:00-15:30 KST.
    market_hours_only: bool = True
    market_open: str = "09:00"
    market_close: str = "15:30"
    # The daytrading screener is an intraday strategy: it is always gated on
    # a live KST session, independent of the market_hours_only switch.
    daytrading_requires_market_open: bool = True

    # --- risk defaults (see trader_core/risk.py for formulas) --------- #
    stop_loss_pct: float = 0.07  # 7% default stop
    take_profit_pct: float = 0.07  # 7% default take profit (raised from 15% on 2026-09-11)
    # Trailing exit: once a position is up ``trailing_arm_pct``, exit when it
    # falls ``trailing_stop_pct`` from its high-water mark.  0 disables.
    # Keep ``arm >= trail`` so the trail is *inside* the arm: the rule exists to
    # protect a profit, and the 3 %/5 % pair broke that -- its trigger sat at
    # ``peak x 0.95`` = -2.15 % for a peak of +3 %, and all five trailing exits
    # of 2026-09-15 closed at a loss or flat (-3.0 / -1.6 / -2.1 / -2.0 / +0.3 %,
    # mean -1.7 %) while their fixed stops allowed -8..-15 %.  3 %/3 % moved the
    # worst armed exit to ``1.03 x 0.97 = -0.09 %`` (breakeven); replaying those
    # same five peaks through it exits at ~+0.8 % (+2.5 %p per trade).
    # Widened 3 %/3 % -> 5.5 %/5 % on 2026-09-21 from the first 30 closed trades:
    # the 3 %/3 % trail was the losing side of the exit structure.  Measured: the
    # 10 trailing exits averaged -0.23 % (i.e. it handed back everything a trade
    # gained) while the 10 fixed stops averaged -10.45 %, and the post-entry
    # high-water mark distribution shows why -- median peak +4.17 %, only 10 % of
    # trades ever reach +1.5x their stop, so a 3 % give-back from a +4 % peak
    # lands at breakeven.  Replaying the 21 replayable trades on 1-minute closes
    # with the holding window fixed, widening the pair is monotone: arm3/trail3
    # +2,883 -> arm4/trail4 +16,680 -> arm5.5/trail5 +25,535 (trail exits 13 -> 6,
    # take-profits 2 -> 7), while tightening (trail 2 %: -37) or narrowing the
    # stop (vol x 0.35: -8,272; fixed 7 %: -9,510) is worse.  5.5 %/5 % keeps the
    # invariant with room: worst armed exit ``1.055 x 0.95 = +0.22 %``, i.e. a
    # trailing exit can no longer be a loss.  Cost of the wider arm: a trade that
    # peaks below +5.5 % is no longer armed, so it rides the fixed stop instead of
    # exiting at breakeven -- pinned by
    # trader_core/tests/test_risk_reinforcements.py::test_peak_below_the_arm_rides_the_fixed_stop.
    trailing_stop_pct: float = 0.05
    trailing_arm_pct: float = 0.055
    kelly_cap: float = 0.25  # absolute cap on the deployed Kelly fraction
    # 0.25 -> 1.0 on 2026-09-28 (사용자 승인, 실계좌 53만원 소액 시작).
    #   왜: 콜드스타트 사전확률(승률 0.53)에서 f* = 0.06 이고, deploy 0.25 를 곱하면
    #   종목당 1.5% = 8,001원(53만원 계좌) -> **10,000원 넘는 종목은 1주도 못 산다**
    #   (sized_qty 실측: 23,103원 0주 · 15,400원 0주 · 10,000원 0주 · 5,000원 1주).
    #   deploy 1.0 이면 종목당 6% = 32,000원 -> 23,103원 1주 · 10,000원 3주 매수 가능.
    #   f* 자체는 그대로라 리스크는 여전히 보수적이다(kelly_cap 0.25 가 상한, 실제 6%).
    #   계좌가 커지면(예: 500만원) 0.25 로 되돌리는 편이 노출 관리에 낫다.
    kelly_fraction: float = 1.0  # fraction of full-Kelly f* actually deployed
    # Priors used when the feed carries no screener_stats (empty-safe).
    # Cold start: ``f* = p - (1-p)/b`` with ``b = avg_win/avg_loss = 1`` is
    # <= 0 for any p <= 0.50, i.e. Kelly itself says "do not bet" -- so a
    # pessimistic prior silently disables trading.  0.53 keeps a thin edge
    # (f* = 0.06 -> 1.5 % of equity per position, half the 0.55 setting) until
    # ``tools/review.py`` replaces the priors with shrunk journal evidence.
    default_win_rate: float = 0.53
    default_avg_win: float = 1.0
    default_avg_loss: float = 1.0

    # Per-day execution cap.  100 -> 3 on 2026-09-28 (사용자 승인, 실계좌 전환): 모의에서는
    # 현금·종목당 상한이 실질 상한이라 100 이 '폭주 방지' 역할만 했지만, 실계좌 첫 운영에서는
    # 하루 100건이 곧 무제한 진입이다. 되돌리려면 이 값만 올린다(모의 복귀 시 100).
    daily_max_trades: int = 3
    # 2026-11-09 사용자 지시: "최대 3종목을 매매할 때 예수금 100%로 매매".
    #   예수금을 1/3씩 균등 배분하는 equal-weight 사이징을 기본으로 켠다
    #   (sizing_mode).  종목당 상한은 1/3(0.3333)이고, 총 노출 상한은 1.0(예수금 100%).
    #   ⚠ 이전 값(0.10 / 0.30)은 Kelly f*·위험예산과 함께 3종목 합계를 ~18-30%로
    #     묶어두었다.  지금 설정은 계좌 전액이 노출될 수 있다 — 손실도 같은 배수로 커진다.
    max_position_pct: float = 1.0 / 3.0  # per-stock, as a fraction of total equity
    # Sizing mode: "kelly" = 기존 Kelly×위험예산(보수적, 종목당 ~6-7%),
    #              "equal_weight" = 남은 예산을 남은 슬롯 수로 나눈 균등 배분(요청 모드).
    sizing_mode: str = "equal_weight"

    max_order_amount: float = 10_000_000.0  # KRW ceiling per single order
    market_order_band_pct: float = 0.10  # +/-10% sanity band (CWE-20)
    # Confirm an entry order at the broker before its journal row claims a
    # position.  The order echo is not evidence: order_no=0 with "이미 처리되었습니다"
    # is returned for orders that filled, for orders that never filled and for
    # duplicates alike (measured live 2026-09-10 424980, 2026-09-17 288180,
    # 2026-09-21 317830 -- the last one filled ~5 minutes after the echo that
    # claimed it already had).  With this on, ``execute()`` reads the working
    # order list and the position list and returns the verdict in
    # ``result["fill_confirm"]``; a partial fill is journaled at the broker's
    # own quantity instead of the planned one.
    confirm_entry_fills: bool = True
    # Portfolio-level brakes.  Kelly sizes ONE bet; it says nothing about the
    # total, so a high entry cap can otherwise pile the whole account into one
    # correlated momentum cluster.  0 (or <= 0) disables each cap.
    # 10 -> 5 on 2026-09-25 (사용자 승인).  이유: 실현 기대값이 **-253원/건**(31건
    # 누적 -7,839원)인 상태에서 노출을 키우면 손실이 증폭된다. 상한을 줄여 손실
    # 노출을 낮추고, 규칙이 검증되면 다시 키운다.  부수효과로 회전이 막혀 있던
    # 슬롯(10/10 포화 → 4일간 신규 진입 0) 압력도 완화된다.
    # ⚠ 청산 규칙(arm/trail/flat_stop/time_stop)은 건드리지 않았다 — 09-21·09-22
    #   1분봉 리플레이로 이미 전수 스윕되어 현행이 최적임이 측정돼 있다
    #   (profiles.py 주석 참조).  특히 time_stop 2세션은 반례(468530: 2일차
    #   -3.4% → 5일차 +33%)로 기각됐다.
    max_open_positions: int = 3  # concurrent *codes* held. 2026-11-09 사용자 지시로 5→3 ("최대 3종목"). 경로별 슬롯은 swing 2 / daytrading 1 로 나눠 한 경로가 3칸을 다 먹지 못하게 한다.
    # 2026-10-07 사용자 요청: **경로별 슬롯** (swing 3 · daytrading 2, close 는 슬롯
    # 미정의 = 전역 cap 사용). 전역 cap 은 3 → 5 로 올린다.
    # 실측 근거(2026-10-07 15:03 저널 스냅샷, journal/trade_journal.sqlite3):
    #   SELECT screener, COUNT(*) FROM trades WHERE exit_ts IS NULL GROUP BY screener
    #   → 미청산 3행 전부 swing(3종목), daytrading 은 사상 0건. swing 3 이 전역 3 을
    #   포화시켜 단타가 단 한 번도 진입하지 못했다. swing 3 + daytrading 2 가 동시에
    #   차도 5 안에 들어오도록 모의 수준(5)으로 되돌린다.
    # 총 노출 max_invested_pct = 0.30 은 **불변** — 슬롯은 '최대 몇 종목'만 정하고,
    # 실제 예산(진입 금액)은 기존 예산 게이트가 그대로 막는다(위험 불변).
    # 규칙·테스트는 trader_core/slot_rules.py (엔진 배선 전에 순수 함수로 잠가 둔다).
    max_open_positions_by_screener: dict = field(
        default_factory=lambda: {"swing": 2, "daytrading": 2}
    )  # 2026-11-09: 전역 3칸 안에서 경로별 상한(합계 4지만 전역 3이 실질 상한)
    max_invested_pct: float = 1.0  # total cost basis as a fraction of equity — 2026-11-09 사용자 지시로 0.30 → 1.0 (예수금 100%)
    # Volatility-aware stop & sizing.  A fixed 7 % stop is inside the daily
    # noise of a momentum list (measured day ranges 19-32 %), so the stop scales
    # with the name's own range and the position shrinks to keep the KRW risk
    # per trade constant.
    stop_range_factor: float = 0.5  # stop = max(stop_loss_pct, factor x day range)
    max_stop_pct: float = 0.20  # never accept a stop wider than this
    risk_per_trade_pct: float = 0.005  # equity at risk per position (0 disables)
    # Exit-price cross-check.  The broker marks every position it reports
    # (``eval_amount / qty`` = its own current price); a live quote that
    # disagrees is a data problem, and acting on it once let a -17.8 % position
    # sit un-stopped while the quote still showed -1 %.  Above this divergence
    # the monitor believes the *worse* of the two numbers.
    quote_divergence_pct: float = 0.01
    # Wall-clock sanity: entries are blocked when the local clock disagrees with
    # an external HTTP Date by more than this.  0 disables the check.
    clock_check_url: str = "https://www.google.com"
    clock_max_skew_seconds: float = 300.0
    clock_check_interval_seconds: float = 600.0
    clock_check_timeout_seconds: float = 8.0
    # Unreachable clock source: False = warn only (Creon data is local, so the
    # loop can still trade correctly offline), True = also block entries.
    clock_check_fail_closed: bool = False

    # --- signal sanity ------------------------------------------------ #
    min_score: float = 0.0  # candidate score must be strictly above this
    order_type: str = "limit"  # default order type for new entries

    # --- screener names ----------------------------------------------- #
    screener_names: List[str] = field(
        default_factory=lambda: ["close", "swing", "daytrading"]
    )

    # --- paths -------------------------------------------------------- #
    kill_switch_path: str = "kill_switch.txt"
    journal_path: str = DEFAULT_JOURNAL_PATH
    dry_run: bool = True

    # ------------------------------------------------------------------ #
    # Layered construction helpers
    # ------------------------------------------------------------------ #
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
                continue  # unknown/foreign keys are ignored
            current = getattr(cfg, name)
            if name == "screener_names":
                coerced = _as_strlist(value, current)  # type: ignore[arg-type]
            elif isinstance(current, dict):
                # Mapping fields (e.g. max_open_positions_by_screener) must stay
                # mappings: the generic ``str(value)`` tail would turn them into
                # "{'swing': 2}" and silently break the slot rules.
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
