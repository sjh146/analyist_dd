"""Runtime configuration for the market-hours runner loop.

:class:`RunnerConfig` carries the loop-specific fields (cycle cadence, rule
thresholds, state/journal paths, dry-run) plus an embedded trader-core
:class:`trader_core.config.Config` for everything the engine/monitor consume
(bridge URL, feed URL, Kelly priors, kill-switch path, ...).

Layering mirrors ``trader_core.config``: defaults < JSON file < environment
(``RUNNER_*`` for loop fields, ``TRADER_CORE_*`` for engine fields) < CLI.
``CLI > env > file > defaults``.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trader_core.config import Config, _ENV_PREFIX as _TRADER_ENV_PREFIX

_RUNNER_ENV_PREFIX = "RUNNER_"

_DEFAULT_JOURNAL = os.path.join("journal", "trade_journal.sqlite3")


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


def _as_profile_map(
    value: Any, default: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Coerce a value into ``{screener: {profile key: value}}``.

    Accepts a dict (config file) or a JSON string (``RUNNER_SCREENER_PROFILES``
    env var).  Anything unrecognisable falls back to the default so a typo can
    never silently drop the strategy profiles (a dropped profile would trade a
    종가 list like a swing list).
    """
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return dict(default)
        try:
            parsed = json.loads(text)
        except ValueError:
            return dict(default)
        raw = parsed if isinstance(parsed, dict) else {}
    else:
        return dict(default)
    result: Dict[str, Dict[str, Any]] = {}
    for key, profile in raw.items():
        if isinstance(profile, dict):
            result[str(key).strip()] = dict(profile)
    return result or dict(default)


def _as_focus_windows(
    value: Any, default: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Coerce ``focus_windows`` from a list of dicts or a JSON string."""
    raw: Any = None
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        text = value.strip()
        if text:
            try:
                raw = json.loads(text)
            except ValueError:
                raw = None
    if not isinstance(raw, list):
        return list(default)
    result: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        window = str(entry.get("window", "")).strip()
        interval = _as_int(entry.get("interval_seconds"), 0)
        if window and interval > 0:
            result.append({"window": window, "interval_seconds": interval})
    return result or list(default)


def _as_age_map(value: Any, default: Dict[str, int]) -> Dict[str, int]:
    """Coerce a value into a ``{screener: max_age_days}`` mapping.

    0 is kept (it means "do not check this screener"), so unlike
    :func:`_as_score_map` nothing is filtered out for being small.
    """
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return dict(default)
        try:
            parsed = json.loads(text)
        except ValueError:
            return dict(default)
        raw = parsed if isinstance(parsed, dict) else {}
    else:
        return dict(default)
    result: Dict[str, int] = {}
    for key, days in raw.items():
        converted = _as_int(days, -1)
        if converted >= 0:
            result[str(key).strip()] = converted
    return result


def _as_score_map(value: Any, default: Dict[str, float]) -> Dict[str, float]:
    """Coerce a value into a ``{screener: threshold}`` mapping."""
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return dict(default)
        try:
            parsed = json.loads(text)
        except ValueError:
            return dict(default)
        raw = parsed if isinstance(parsed, dict) else {}
    else:
        return dict(default)
    result: Dict[str, float] = {}
    for key, threshold in raw.items():
        converted = _as_float(threshold, 0.0)
        if converted > 0:
            result[str(key).strip()] = converted
    return result


@dataclass
class RunnerConfig:
    """Configuration for the market-hours operating loop.

    ``engine`` embeds the trader-core :class:`Config`; runner-owned fields
    that overlap engine fields (``market_open``/``market_close``/``dry_run``/
    ``journal_path``) are kept in sync via :meth:`engine_config`.
    """

    # --- loop cadence / schedule (KST) -------------------------------- #
    interval_seconds: int = 300  # full cycle cadence while market is open
    # Flushed file log for detached runs ("" -> runner.log in the CWD).
    log_path: str = "runner.log"
    # Shorter cadence inside these windows (the loop still runs the same
    # pipeline, it just looks more often).  Each cycle costs broker requests,
    # so only the windows that matter get the fast cadence.
    focus_windows: List[Dict[str, Any]] = field(
        default_factory=lambda: [
            {"window": "09:00-09:30", "interval_seconds": 60},
            {"window": "13:00-14:00", "interval_seconds": 60},
            {"window": "14:30-15:30", "interval_seconds": 30},
        ]
    )
    market_open: str = "09:00"
    market_close: str = "15:30"
    start_before_open_min: int = 5  # schedule window opens this many min early
    end_after_close_min: int = 10  # ... and closes this many min after close

    # --- sub-cycle exit watch (realtime cache) ------------------------- #
    # Between cycles the loop can look at the ticks the bridge already holds
    # (GET /quotes with no codes = the StockCur cache, **no Daishin request**)
    # and react to an exit-line breach within ~1 s instead of waiting for the
    # next cycle.  That is how "reaction time = 30-300 s" becomes "reaction time
    # = one tick poll" without spending the per-second request budget -- which
    # is exactly why the base cadence is slow.
    # Safety: a cached tick is only a *candidate*.  The loop confirms every
    # breach against the broker mark (one /positions call) and believes the
    # worse (lower) price before selling (2026-09-10: cache 6,050 vs broker
    # 5,040 left a -17.8 % position un-stopped).  0 disables the watch.
    tick_watch_seconds: float = 1.0
    # Ignore ticks older than this (a stalled realtime feed must not fire).
    tick_watch_max_age_seconds: float = 5.0

    # --- screeners (v1 trades close + swing only) --------------------- #
    screeners: List[str] = field(default_factory=lambda: ["close", "swing"])

    # --- per-screener strategy profiles ------------------------------- #
    # Each candidate list is traded with its own mechanic (entry window,
    # exit mode, sizing).  Keys are screener names; see
    # trader_core/profiles.py for the defaults and the meaning of each key.
    screener_profiles: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # --- R1 regime proxy ---------------------------------------------- #
    r1_top_n: int = 10
    # Per-screener minimum top-N average score.  A screener absent from this
    # dict has R1 skipped for it.
    r1_min_avg_score: Dict[str, float] = field(
        default_factory=lambda: {"close": 88.0, "swing": 75.0}
    )
    # Same top-N average, in *probability* units.  Used instead of
    # ``r1_min_avg_score`` when the feed declares ``score_kind ==
    # "calibrated_prob"``, i.e. ``score`` is a model probability x 100.
    #
    # 2026-09-29 실측: swing 피드의 score 는 모델 confidence(0.5038~0.6245)였는데
    # 문턱은 0~100 점수 스케일 75 였다 → 상위10 평균 55.0 < 75 로 swing 경로가
    # 구조적으로 닫혀 있었다(모의 7일 신규 진입 0의 직접 원인).  확률 스케일에서는
    # 0~1 문턱으로 비교한다 — 0.58 = 상위10 평균 확률 58%.
    r1_min_avg_prob: Dict[str, float] = field(
        default_factory=lambda: {"swing": 0.58}
    )
    # 백분위 스케일 문턱(0~100). 피드가 ``score_kind == "rank_pct"`` 를 선언할 때 적용된다
    # (계획 ③: 확률 절대값은 모델 분포가 이동하면 의미를 잃는다 — 백분위는 "모델 자체
    # 순위에서 상위 몇 %"라 강건하다). 비어 있으면 ``r1_min_avg_score`` 로 판단한다
    # (둘 다 0~100 스케일이라 문턱 값의 의미가 유지된다).
    r1_min_avg_rank_pct: Dict[str, float] = field(default_factory=dict)
    # 후보별 신선도 상한(역일).  signal_date 로부터 이보다 오래된 후보는 그 후보만
    # 버린다(리스트 전체를 비우지 않는다).  피드가 보낸 ``valid_until`` 이 있으면
    # 그쪽도 함께 적용된다(둘 중 하나라도 지나면 stale).
    # 왜: 게이트 1 은 generated_at(발행 시각)만 봐서, 산출물이 새것이어도 종목별 신호가
    # 며칠 전이면 그대로 매수됐다(2026-09-29: close 후보 signal_date 09-23 을 09-29 에
    # 매수할 수 있는 상태였다).  0/미지정 스크리너는 검사하지 않는다.
    # close 4일 = 금→화(월 휴장) 정상 케이스 통과 + 6일 지난 신호 차단.
    # swing 5일 = 모델 라벨 지평 h5 와 같은 길이.
    candidate_max_age_days: Dict[str, int] = field(
        default_factory=lambda: {"close": 4, "swing": 5}
    )

    # --- R1 / heat gauge ---------------------------------------------- #
    # R1 only checks a floor ("strong enough").  These are the ceiling: above
    # them the list is late/euphoric, not trending (2026-09-10: avg score 93.7
    # and +19.7 % on the day -> the entries lost 31,455 KRW).  0 disables.
    r1_max_avg_score: float = 95.0
    r1_max_avg_pct: float = 15.0
    # Daily entry cap.  100 -> 3 on 2026-09-28 (사용자 승인, 실계좌 전환).  모의에서는
    # 현금·종목당 상한이 실질 상한이라 100 이 폭주 방지용이었지만, 실계좌 첫 운영에서는
    # 하루 3건이 회전·수수료를 통제하는 실제 상한이다.  engine 의 daily_max_trades(3)와
    # 같은 값으로 맞춰 두었다 — 둘 중 하나만 낮으면 다른 쪽이 먼저 걸려 원인이 헷갈린다.
    r2_max_entries_per_day: int = 3
    # Anti-churn: never buy the same code twice in one day.  Without this a
    # large cap + short focus cadence would re-buy the same candidate list on
    # every 60s cycle until cash runs out.
    r2_one_entry_per_code_per_day: bool = True
    # Grace period before an unconfirmed entry is declared "never filled" and
    # its concurrent-position slot is freed.  Broker positions settle seconds
    # to ~1 minute after a fill (measured 3 -> 37 shares in ~90s), so an order
    # that just filled can be gone from the working list while the position is
    # not visible yet; closing that row at cost would throw away a real
    # holding's own stop/take-profit levels.
    pending_entry_grace_s: float = 120.0
    r3_max_deviation_pct: float = 0.03  # intraday move vs planned close price
    r4_loss_limit_pct: float = 0.03  # daily equity drawdown that self-halts

    # --- R5 news gate (BC-250 monitor feed) -------------------------- #
    # Per-candidate news/sentiment veto fetched from the BC-250 news-gate
    # HTTP endpoint before ACT.  Empty URL disables the gate; when enabled
    # and unreachable the loop fails closed (no new entries) unless
    # ``news_gate_fail_closed`` is False.
    # 로컬 스택의 뉴스 게이트(scripts/news_gate_server.py). 2026-09-29 이전 기본값은
    # 구 프로덕션 노드 100.127.186.48:8082 였는데 그 노드는 09-07부터 offline → 모든
    # 사이클이 R5 fail-closed 로 신규 진입을 전면 차단하고 있었다(실측: 매 사이클
    # "r5: news gate unavailable ... new entries blocked").
    news_gate_url: str = "http://127.0.0.1:8082/newsgate"
    news_gate_fail_closed: bool = True
    news_gate_window_days: int = 3

    # --- failure handling --------------------------------------------- #
    max_consecutive_check_failures: int = 3
    # A connectivity halt (max_consecutive_check_failures reached) pauses the
    # loop instead of ending it: the process keeps cycling, and as soon as the
    # bridge answers again the halt clears itself and trading resumes without a
    # human typing ``resume``.  R4's loss halt and the kill-switch file stay
    # hard stops.  Set False for the old "halt and exit" behaviour.
    auto_resume_check_halt: bool = True

    # --- paths / notifications ---------------------------------------- #
    state_path: str = "loop_state.json"
    journal_path: str = _DEFAULT_JOURNAL  # the loop ALWAYS journals
    webhook_url: str = ""  # Discord; empty means log-only notification
    dry_run: bool = True  # CLI default; --live must be explicit
    # Self-tuning file written by tools/review.py (journal -> parameters).
    # When present it may override ``screeners`` and ``r1_min_avg_score`` via
    # :meth:`apply_params`, and it feeds the engine-side filters/Kelly priors
    # through ``engine.rank_params_path``.
    params_path: str = "strategy_params.json"

    # --- post-close reconciliation ------------------------------------ #
    # Once the day is closed (and outstanding orders are cancelled) compare the
    # journal with the broker, because the next morning must not start on a
    # lie: an order the bridge echoed as "filled" but the broker never filled
    # leaves a phantom position that eats an entry slot and the per-code entry
    # guard, and a session that dropped mid-cycle leaves silent drift.
    close_reconcile: bool = True
    # Apply the corrections where the *broker* is the authority (order never
    # filled -> close at cost/zero PnL; partial fill -> take the broker's
    # qty/price; broker-only position -> adopt at broker cost; phantom close ->
    # reopen).  A journal position the broker is flat on with no unfilled order
    # is only ever REPORTED: its real exit is a broker fill price and the live
    # quote is not one, so guessing it would book a fabricated P&L.
    close_reconcile_apply: bool = True

    # --- embedded trader-core config ---------------------------------- #
    engine: Config = field(default_factory=Config)

    # ------------------------------------------------------------------ #
    # Layered construction helpers
    # ------------------------------------------------------------------ #
    @classmethod
    def env_overrides(cls, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Return raw ``RUNNER_*`` + ``TRADER_CORE_*`` env vars by field name.

        Only variables that are present are returned (so callers can layer env
        over file/CLI without clobbering unset fields).  Runner loop fields
        honour the ``RUNNER_`` prefix; embedded trader-core fields keep the
        ``TRADER_CORE_`` prefix (exactly like ``Config.env_overrides``).
        """
        source = os.environ if env is None else env
        result: Dict[str, Any] = {}
        # Trader-core fields first; runner-owned fields then override so the
        # loop's own prefix wins for overlapping names (RUNNER_DRY_RUN beats
        # TRADER_CORE_DRY_RUN) regardless of os.environ ordering.
        for key, value in source.items():
            if key.startswith(_TRADER_ENV_PREFIX):
                name = key[len(_TRADER_ENV_PREFIX):].lower()
                if name in _ENGINE_FIELD_NAMES:
                    result[name] = value
        for key, value in source.items():
            if key.startswith(_RUNNER_ENV_PREFIX):
                name = key[len(_RUNNER_ENV_PREFIX):].lower()
                if name in _FIELD_NAMES:
                    result[name] = value
        return result

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "RunnerConfig":
        """Build a :class:`RunnerConfig` honouring ``RUNNER_*`` env vars."""
        return cls.from_mapping(cls.env_overrides(env))

    @classmethod
    def from_file(cls, path: str) -> "RunnerConfig":
        """Build a :class:`RunnerConfig` from a JSON file (field subset)."""
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("config file must contain a JSON object")
        return cls.from_mapping({k.lower(): v for k, v in raw.items()})

    @classmethod
    def from_mapping(cls, data: Dict[str, Any]) -> "RunnerConfig":
        """Build a :class:`RunnerConfig` from a flat field-name mapping.

        Keys are routed by owner: runner-owned fields land on the loop config,
        engine-owned fields (including overlapping names) land on the embedded
        trader-core :class:`Config`.  Unknown keys are ignored.
        """
        cfg = cls()
        engine_kw: Dict[str, Any] = {}
        for name, value in data.items():
            if name in _ENGINE_FIELD_NAMES:
                engine_kw[name] = value
            if name not in _FIELD_NAMES:
                continue
            current = getattr(cfg, name)
            if name == "screeners":
                coerced = _as_strlist(value, current)  # type: ignore[arg-type]
            elif name == "focus_windows":
                coerced = _as_focus_windows(value, current)  # type: ignore[arg-type]
            elif name == "screener_profiles":
                coerced = _as_profile_map(value, current)  # type: ignore[arg-type]
            elif name == "r1_min_avg_score":
                coerced = _as_score_map(value, current)  # type: ignore[arg-type]
            elif name == "r1_min_avg_prob":
                coerced = _as_score_map(value, current)  # type: ignore[arg-type]
            elif name == "r1_min_avg_rank_pct":
                coerced = _as_score_map(value, current)  # type: ignore[arg-type]
            elif name == "candidate_max_age_days":
                coerced = _as_age_map(value, current)  # type: ignore[arg-type]
            elif isinstance(current, bool):
                coerced = _as_bool(value, current)
            elif isinstance(current, int):
                coerced = _as_int(value, current)
            elif isinstance(current, float):
                coerced = _as_float(value, current)
            else:
                coerced = str(value)
            setattr(cfg, name, coerced)
        cfg.engine = Config._coerce(engine_kw)
        return cfg

    def with_overrides(self, **overrides: Any) -> "RunnerConfig":
        """Return a copy of this config with ``overrides`` applied."""
        return RunnerConfig.from_mapping({**self.as_dict(), **overrides})

    def engine_config(self) -> Config:
        """Return the trader-core :class:`Config` the engine/monitor use.

        Runner-owned schedule/safety fields are copied onto the embedded
        config so the engine gates (market hours, screener list, dry-run) and
        the journal always agree with the loop's own settings.
        """
        return self.engine.with_overrides(
            dry_run=self.dry_run,
            journal_path=self.journal_path,
            market_open=self.market_open,
            market_close=self.market_close,
            screener_names=list(self.screeners),
        )

    def apply_params(self, path: Optional[str] = None) -> "RunnerConfig":
        """Apply tuned loop-side parameters from the self-review file.

        Recognised keys (all optional, everything else is ignored):

        * ``"screeners"``         -> the screener list the loop trades
        * ``"r1_min_avg_score"``  -> per-screener R1 thresholds
        * ``"r1_min_avg_prob"``   -> per-screener R1 thresholds in probability
          units (used when the feed declares ``score_kind=calibrated_prob``)
        * ``"r1_min_avg_rank_pct"`` -> per-screener R1 thresholds in percentile
          units (used when the feed declares ``score_kind=rank_pct``)
        * ``"candidate_max_age_days"`` -> per-screener candidate staleness cap

        The file is written by ``tools/review.py`` from the trade journal, so a
        review cycle can raise or lower the entry bar without editing config.
        A missing or malformed file leaves the config untouched (fail-open on
        tuning, fail-closed on risk).
        """
        target = path or self.params_path
        if not target:
            return self
        try:
            with open(target, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return self
        if not isinstance(raw, dict):
            return self
        overrides: Dict[str, Any] = {}
        screeners = raw.get("screeners")
        if isinstance(screeners, list) and screeners:
            values = [str(item).strip() for item in screeners if str(item).strip()]
            if values:
                overrides["screeners"] = values
        thresholds = raw.get("r1_min_avg_score")
        if isinstance(thresholds, dict) and thresholds:
            cleaned = {}
            for key, value in thresholds.items():
                number = _as_float(value, 0.0)
                if number > 0:
                    cleaned[str(key).strip()] = number
            if cleaned:
                overrides["r1_min_avg_score"] = cleaned
        prob_thresholds = raw.get("r1_min_avg_prob")
        if isinstance(prob_thresholds, dict) and prob_thresholds:
            cleaned = {}
            for key, value in prob_thresholds.items():
                number = _as_float(value, 0.0)
                if number > 0:
                    cleaned[str(key).strip()] = number
            if cleaned:
                overrides["r1_min_avg_prob"] = cleaned
        rank_thresholds = raw.get("r1_min_avg_rank_pct")
        if isinstance(rank_thresholds, dict) and rank_thresholds:
            cleaned = {}
            for key, value in rank_thresholds.items():
                number = _as_float(value, 0.0)
                if number > 0:
                    cleaned[str(key).strip()] = number
            if cleaned:
                overrides["r1_min_avg_rank_pct"] = cleaned
        age_limits = raw.get("candidate_max_age_days")
        if isinstance(age_limits, dict) and age_limits:
            cleaned_ages = {}
            for key, value in age_limits.items():
                days = _as_int(value, -1)
                if days >= 0:
                    cleaned_ages[str(key).strip()] = days
            if cleaned_ages:
                overrides["candidate_max_age_days"] = cleaned_ages
        if not overrides:
            return self
        return self.with_overrides(**overrides)

    def as_dict(self) -> Dict[str, Any]:
        """Return the config as a flat dict (useful for JSON output)."""
        result: Dict[str, Any] = {}
        for name in _FIELD_ORDER:
            result[name] = getattr(self, name)
        for name, value in self.engine.as_dict().items():
            if name not in _FIELD_NAMES:
                result[name] = value
        return result

    def as_engine_dict(self) -> Dict[str, Any]:
        """Return the effective engine config as a dict."""
        return self.engine_config().as_dict()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RunnerConfig({0})".format(
            ", ".join(
                "{0}={1!r}".format(name, getattr(self, name))
                for name in _FIELD_ORDER
            )
        )


_FIELD_ORDER = tuple(
    f.name for f in dataclasses.fields(RunnerConfig) if f.name != "engine"
)
_FIELD_NAMES = frozenset(_FIELD_ORDER)
_ENGINE_FIELD_NAMES = frozenset(Config().as_dict().keys())
