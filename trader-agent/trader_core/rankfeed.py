"""Self-generated candidate source: Creon ranking -> :class:`FeedSnapshot`.

The engine only ever consumes a ``FeedSnapshot`` (``INTEGRATION_MAP.md`` §8a), so
"no external feed yet" is solved by producing that snapshot ourselves from the
broker's own 당일 상승률 ranking:

1. ``GET /rank``      -- ranked rising stocks (bridge -> ``CpSysDib.CpSvrNew7043``)
2. filter             -- drop 상한가, thin volume, negligible gain, bad codes
3. ``GET /quotes``    -- live price/turnover for the shortlist (top-N only)
4. score              -- documented 0..100 momentum score
5. ``FeedSnapshot``   -- candidates + journal-derived Kelly priors

Request budget: one ``/rank`` call (1-3 pages at these limits) plus **one quote
per shortlisted code**.  Both are gated by the bridge's
``GetLimitRemainCount`` pacing, and the shortlist is deliberately small.

Self-tuning: :func:`load_params` reads ``strategy_params.json`` (written by
``tools/review.py`` from the trade journal).  It can move the filters, the
scoring references and — most importantly — the Kelly priors that the engine
reads from ``scoring_stats`` (``engine._prior_stats``), which is how the
position sizing adapts to realised results instead of a constant.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional

from .bridge_client import BridgeError
from .config import Config, now_kst
from .feed import FeedError, FeedSnapshot, normalize_code

# 대비부호 arrives as an ASCII char code (49..53) like every other Cybos char
# field (see bridge/api_notes.md §11.2): 1=상한 2=상승 3=보합 4=하한 5=하락.
_SIGN_PLUS = {"1", "2"}
_SIGN_MINUS = {"4", "5"}

# Scoring weights (sum = 1.0) and reference points.  Overridable via params.
_W_PCT = 0.40      # 등락 강도
_W_VOLUME = 0.30   # 당일 거래량
_W_RANK = 0.20     # 랭킹 내 위치
_W_TURNOVER = 0.10  # 거래대금(유동성)
_REF_PCT = 10.0
_REF_VOLUME = 20_000_000.0
_REF_TURNOVER = 200_000.0  # 백만원 단위 (StockMst 'amount' is 백만원)
_LIMIT_UP_PENALTY = 0.6

DEFAULT_PARAMS: Dict[str, Any] = {
    "min_pct": 2.0,
    # Chase ceiling: skip names already up more than this on the day.  A
    # momentum ranker's whole list is "already up", so without a ceiling the
    # late entries buy the top of the move (measured 2026-09-10: entries at
    # 14:55 into names up 15-29 %).  0 disables.
    # Raised 12 -> 15 on 2026-09-11 together with ``rank_pct_to`` (30 -> 15):
    # the ceiling alone was dead weight, because a 30 % ranking window is
    # sorted descending, so the sampled head sat *above* the ceiling and the
    # feed produced 0 candidates all morning.  The pair now samples the
    # +12..15 % band, where the pullback guard still has room to work.
    "max_pct": 15.0,
    # Pullback requirement: skip names sitting within this much of the day's
    # high (measured 2026-09-10: entries averaged -4.6 % from the high, i.e.
    # buying the top).  0 disables.
    "min_pullback_pct": 3.0,
    "min_volume": 100_000,
    "skip_limit_up": True,
    # Shortlist depth.  Each shortlisted code costs one quote request per
    # cycle, so this is the request-budget knob (8 names = 8 requests/cycle).
    # 6 -> 8 on 2026-09-11: with the window narrowed to rank_pct_to=15 the
    # extra rows are still inside the buy band.
    "top_n": 8,
    # "percentile" scores candidates against each other inside one snapshot
    # (always spreads 0..100); "absolute" is the legacy fixed-reference formula
    # which saturated at 88-99 and could not discriminate.
    "scoring": "percentile",
    "pct_ref": _REF_PCT,
    "volume_ref": _REF_VOLUME,
    "turnover_ref": _REF_TURNOVER,
    "limit_up_penalty": _LIMIT_UP_PENALTY,
}


def load_params(path: str) -> Dict[str, Any]:
    """Return the tuned parameters, falling back to :data:`DEFAULT_PARAMS`.

    A missing or malformed file is not an error: the system keeps trading on
    defaults (fail-open on *parameters*, fail-closed on *risk*).
    """
    params = dict(DEFAULT_PARAMS)
    if not path:
        return params
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return params
    if not isinstance(raw, dict):
        return params
    for key in list(DEFAULT_PARAMS):
        if key in raw:
            params[key] = raw[key]
    for key in ("prior_win_rate", "prior_avg_win_pct", "prior_avg_loss_pct"):
        if key in raw:
            params[key] = raw[key]
    return params


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sign_char(value: Any) -> str:
    """Return the 대비부호 as a single character (``'1'``..``'5'``)."""
    if isinstance(value, int):
        return chr(value) if 0 < value < 256 else ""
    text = str(value or "").strip()
    return text[:1]


def pct_change(current: Any, diff: Any, diff_sign: Any) -> Optional[float]:
    """Return the day's percentage move from price, 대비 and 대비부호.

    ``prev_close = current - signed_diff`` (StockMst ``current`` minus the
    ranking's 대비), so a +1,250 move on a 26,750 price is ~4.90 %.
    """
    price = _as_float(current)
    delta = _as_float(diff)
    if price is None or delta is None:
        return None
    sign = sign_char(diff_sign)
    if sign in _SIGN_MINUS:
        delta = -abs(delta)
    elif sign in _SIGN_PLUS:
        delta = abs(delta)
    previous = price - delta
    if previous <= 0:
        return None
    return delta / previous * 100.0


def momentum_score(
    pct: Optional[float],
    volume: Any,
    turnover: Any,
    position: int,
    count: int,
    limit_up: bool,
    params: Optional[Dict[str, Any]] = None,
) -> float:
    """Return a 0..100 momentum score (deterministic, documented).

    ``score = 100 * (0.40*pct_c + 0.30*volume_c + 0.20*rank_c + 0.10*turnover_c)``
    with every component clamped to 0..1 and 상한가 rows multiplied by
    ``limit_up_penalty`` so an untradeable limit-up name never outranks a
    tradeable mover.
    """
    cfg = dict(DEFAULT_PARAMS)
    if params:
        cfg.update({k: v for k, v in params.items() if k in cfg})

    def clamp(value: float) -> float:
        return max(0.0, min(1.0, value))

    pct_ref = float(cfg.get("pct_ref") or _REF_PCT)
    volume_ref = float(cfg.get("volume_ref") or _REF_VOLUME)
    turnover_ref = float(cfg.get("turnover_ref") or _REF_TURNOVER)

    pct_c = clamp((pct or 0.0) / pct_ref) if pct_ref > 0 else 0.0
    vol = _as_float(volume) or 0.0
    vol_ref = _as_float(volume_ref) or 0.0
    vol_c = clamp(
        math.log1p(vol) / math.log1p(vol_ref) if vol_ref > 0 else 0.0
    )
    rank_c = clamp((count - position + 1) / count) if count > 0 else 0.0
    turn = _as_float(turnover) or 0.0
    turn_ref = _as_float(turnover_ref) or 0.0
    turn_c = clamp(turn / turn_ref) if turn_ref > 0 else 0.0

    score = 100.0 * (
        _W_PCT * pct_c + _W_VOLUME * vol_c + _W_RANK * rank_c + _W_TURNOVER * turn_c
    )
    if limit_up:
        score *= float(cfg.get("limit_up_penalty") or _LIMIT_UP_PENALTY)
    return round(score, 2)


def percentile_ranks(values: List[float]) -> List[float]:
    """Return each value's percentile (0..1) inside *values*, ties averaged.

    ``max`` maps to 1.0 and ``min`` to 0.0; a single value maps to 1.0.  Used by
    :func:`percentile_scores` so a snapshot always spreads across the whole
    0..100 range no matter how hot the day is.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    index = 0
    while index < n:
        end = index
        while end + 1 < n and values[order[end + 1]] == values[order[index]]:
            end += 1
        # average position of the tied block, normalised to 0..1
        average = (index + end) / 2.0
        for position in range(index, end + 1):
            ranks[order[position]] = average / (n - 1)
        index = end + 1
    return ranks


def percentile_scores(
    metrics: List[Dict[str, Any]], params: Optional[Dict[str, Any]] = None
) -> List[float]:
    """Score a whole snapshot's candidates 0..100 by within-set percentile.

    ``metrics`` items carry ``pct``, ``volume``, ``turnover``, ``position`` and
    ``limit_up``.  Each factor becomes its percentile inside this snapshot
    (strongest = 1.0), weighted exactly like the legacy formula, so the result
    keeps the same meaning while actually discriminating: the best candidate of
    the set scores ~100 and the worst ~0.
    """
    cfg = dict(DEFAULT_PARAMS)
    if params:
        cfg.update({k: v for k, v in params.items() if k in cfg})
    if not metrics:
        return []

    pctiles = percentile_ranks([float(m.get("pct") or 0.0) for m in metrics])
    volumes = percentile_ranks(
        [math.log1p(max(0.0, float(m.get("volume") or 0.0))) for m in metrics]
    )
    turnovers = percentile_ranks(
        [max(0.0, float(m.get("turnover") or 0.0)) for m in metrics]
    )
    # rank 1 (best) must be the highest percentile -> invert the position
    positions = [float(m.get("position") or 0.0) for m in metrics]
    rank_pctiles = percentile_ranks([-value for value in positions])

    penalty = float(cfg.get("limit_up_penalty") or _LIMIT_UP_PENALTY)
    scores: List[float] = []
    for index, metric in enumerate(metrics):
        score = 100.0 * (
            _W_PCT * pctiles[index]
            + _W_VOLUME * volumes[index]
            + _W_RANK * rank_pctiles[index]
            + _W_TURNOVER * turnovers[index]
        )
        if metric.get("limit_up"):
            score *= penalty
        scores.append(round(score, 2))
    return scores


def _filter_summary(params: Dict[str, Any]) -> Dict[str, Any]:
    """The filter/scoring settings behind this snapshot (trade attribution)."""
    keys = (
        "min_pct", "max_pct", "min_pullback_pct", "min_volume",
        "skip_limit_up", "top_n", "scoring",
    )
    return {key: params[key] for key in keys if key in params}


class RankFeedClient:
    """Builds a :class:`FeedSnapshot` from the bridge's ranking endpoint."""

    def __init__(
        self,
        bridge: Any,
        config: Optional[Config] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Construct the client.

        :param bridge: anything exposing ``rank(...)`` and ``quotes([...])``
            (``BridgeClient`` in production, a fake in tests).
        :param config: engine config (screener name, rank knobs, params path).
        :param params: pre-loaded params; when omitted the params file from the
            config is loaded lazily on every ``fetch()`` so a live tuning cycle
            (``tools/review.py``) takes effect without a restart.
        """
        self.bridge = bridge
        self.config = config or Config()
        self._params = params

    # ------------------------------------------------------------------ #
    def params(self) -> Dict[str, Any]:
        if self._params is not None:
            return dict(self._params)
        return load_params(getattr(self.config, "rank_params_path", ""))

    def fetch(self) -> FeedSnapshot:
        """Return one freshly generated snapshot (raises :class:`FeedError`)."""
        cfg = self.config
        params = self.params()
        screener = getattr(cfg, "rank_screener", "rank_momentum")

        try:
            ranking = self.bridge.rank(
                limit=int(getattr(cfg, "rank_limit", 60)),
                direction="up",
                pct_from=0,
                pct_to=int(getattr(cfg, "rank_pct_to", 30)),
                sort=int(getattr(cfg, "rank_sort", 21)),
            )
        except BridgeError as exc:
            raise FeedError("rank fetch failed: {0}".format(exc))
        except OSError as exc:
            raise FeedError("rank fetch failed: {0}".format(exc))

        rows = [row for row in (ranking.get("items") or []) if isinstance(row, dict)]

        shortlist: List[Dict[str, Any]] = []
        for position, row in enumerate(rows, start=1):
            code = normalize_code(row.get("code"))
            if code is None:
                continue
            sign = sign_char(row.get("diff_sign"))
            limit_up = sign == "1"
            if limit_up and bool(params.get("skip_limit_up", True)):
                continue
            volume = _as_float(row.get("volume")) or 0.0
            if volume < float(params.get("min_volume", 0) or 0):
                continue
            shortlist.append(
                {"code": code, "row": row, "position": position, "limit_up": limit_up}
            )
            if len(shortlist) >= int(params.get("top_n", 6) or 6):
                break

        if not shortlist:
            return self._snapshot(screener, [], params)

        codes = [entry["code"] for entry in shortlist]
        try:
            quotes = self.bridge.quotes(codes)
        except BridgeError as exc:
            raise FeedError("quote fetch for ranking failed: {0}".format(exc))
        except OSError as exc:
            raise FeedError("quote fetch for ranking failed: {0}".format(exc))

        total = len(rows) or 1
        # Pass 1: apply the price filters and collect what the scorer needs.
        passing: List[Dict[str, Any]] = []
        for entry in shortlist:
            quote = quotes.get(entry["code"]) or {}
            row = entry["row"]
            current = _as_float(quote.get("current"))
            pct = pct_change(quote.get("current"), row.get("diff"), row.get("diff_sign"))
            if pct is None or pct < float(params.get("min_pct", 0.0) or 0.0):
                continue
            # Chase guard: a momentum list is *already* up; past a ceiling the
            # entry is buying someone else's profit-taking supply.  0/absent
            # disables the ceiling.
            ceiling = float(params.get("max_pct", 0.0) or 0.0)
            if ceiling > 0 and pct > ceiling:
                continue
            # Pullback guard: never buy within ``min_pullback_pct`` of the day's
            # high (measured: entries averaged -4.6 % from the high, i.e. the
            # top of the move).  Needs the day high, so it silently passes when
            # the quote lacks one.
            day_high = _as_float(quote.get("high"))
            day_low = _as_float(quote.get("low"))
            pullback = float(params.get("min_pullback_pct", 0.0) or 0.0)
            if pullback > 0 and day_high and day_high > 0 and current:
                off_high = (day_high - current) / day_high * 100.0
                if off_high < pullback:
                    continue
            passing.append(
                {
                    "entry": entry,
                    "row": row,
                    "quote": quote,
                    "pct": pct,
                    "current": current,
                    "day_high": day_high,
                    "day_low": day_low,
                }
            )

        # Pass 2: score the survivors against each other (percentile scoring) or
        # with the legacy absolute formula.
        scoring = str(params.get("scoring") or "percentile").lower()
        if scoring == "absolute":
            scores = [
                momentum_score(
                    pct=item["pct"],
                    volume=item["row"].get("volume"),
                    turnover=item["quote"].get("amount"),
                    position=item["entry"]["position"],
                    count=total,
                    limit_up=item["entry"]["limit_up"],
                    params=params,
                )
                for item in passing
            ]
        else:
            scores = percentile_scores(
                [
                    {
                        "pct": item["pct"],
                        "volume": _as_float(item["row"].get("volume")) or 0.0,
                        "turnover": _as_float(item["quote"].get("amount")) or 0.0,
                        "position": item["entry"]["position"],
                        "limit_up": item["entry"]["limit_up"],
                    }
                    for item in passing
                ],
                params=params,
            )

        candidates = []
        for item, score in zip(passing, scores):
            entry = item["entry"]
            row = item["row"]
            quote = item["quote"]
            day_range_pct = 0.0
            if item["day_high"] and item["day_low"] and item["day_low"] > 0:
                day_range_pct = (item["day_high"] - item["day_low"]) / item["day_low"] * 100.0
            candidates.append(
                {
                    "rank": entry["position"],
                    "stock_code": entry["code"],
                    "stock_name": str(row.get("name") or quote.get("name") or ""),
                    "signal_date": now_kst().strftime("%Y-%m-%d"),
                    "close_price": str(quote.get("current") or ""),
                    "score": "{0:.2f}".format(score),
                    "reason": "{0}위 +{1:.2f}% 거래량 {2:,}".format(
                        entry["position"], item["pct"],
                        int(_as_float(row.get("volume")) or 0),
                    ),
                    "sector": "",
                    "pct_change": "{0:.2f}".format(item["pct"]),
                    "volume": str(int(_as_float(row.get("volume")) or 0)),
                    "diff": str(row.get("diff")),
                    "diff_sign": sign_char(row.get("diff_sign")),
                    "limit_up": entry["limit_up"],
                    "turnover": str(quote.get("amount") or ""),
                    # Volatility inputs for the engine's range-based stop/sizing.
                    "day_high": str(item["day_high"] or ""),
                    "day_low": str(item["day_low"] or ""),
                    "day_range_pct": "{0:.2f}".format(day_range_pct),
                }
            )

        return self._snapshot(screener, candidates, params)

    # ------------------------------------------------------------------ #
    def _snapshot(
        self, screener: str, items: List[Dict[str, Any]], params: Dict[str, Any]
    ) -> FeedSnapshot:
        """Build a snapshot **through the feed parser**.

        Constructing ``FeedSnapshot`` directly would put raw dicts into
        ``candidates`` while the engine expects :class:`~trader_core.feed.Candidate`
        objects (``candidate.stock_code``/``.score``/...).  Round-tripping the
        payload through :meth:`FeedSnapshot.from_payload` keeps this source
        byte-identical in shape to the external screener feed.
        """
        return FeedSnapshot.from_payload(
            {
                "generated_at": now_kst().isoformat(),
                "source": "creon-rank",
                "candidates": {screener: {"items": items}},
                "filters": _filter_summary(params),
                "scoring_summary": {
                    "screener_stats": self._scoring_stats(screener, params)
                },
            }
        )

    def _scoring_stats(self, screener: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Return ``{screener: {...}}`` Kelly priors from the tuned params.

        Only keys that are actually present are emitted: the engine falls back
        to its config priors for anything missing, so an untuned system behaves
        exactly as before.
        """
        stats: Dict[str, Any] = {}
        for key in ("prior_win_rate", "prior_avg_win_pct", "prior_avg_loss_pct"):
            value = params.get(key)
            if value is None:
                continue
            number = _as_float(value)
            if number is None:
                continue
            if key == "prior_win_rate":
                stats["win_rate"] = number * 100.0 if number <= 1.0 else number
            elif key == "prior_avg_win_pct":
                stats["avg_win_pct"] = number
            else:
                stats["avg_loss_pct"] = number
        return {screener: stats} if stats else {}


def build_snapshot_from_rank(
    bridge: Any, config: Optional[Config] = None
) -> FeedSnapshot:
    """Convenience wrapper used by the CLIs and the review tool."""
    return RankFeedClient(bridge, config).fetch()


# R1 floor used for the self-generated screener when the params file does not
# carry one.  Percentile scoring rates candidates *inside their own snapshot*,
# so a shortlist that IS the whole candidate set always averages ~50 (measured
# 2026-09-11: 6 candidates scored 84/58/50/44/40/24 -> avg 50.0, and the old
# 55.0 floor blocked every entry the fixed ranking feed produced).  Any floor
# >= 50 can only ever block and any floor < 50 never fires, so this value is
# inert by construction: the "too hot" judgement for the rank feed belongs to
# the pct-based guards (r1_max_avg_pct heat gauge, max_pct, min_pullback_pct).
DEFAULT_R1_MIN_AVG_SCORE = 45.0


def make_feed_client(bridge: Any, config: Optional[Config] = None) -> Any:
    """Return the candidate source selected by ``config.feed_source``.

    * ``"rank"`` -> :class:`RankFeedClient` (self-generated from the broker's
      ranking; needs no external machine)
    * anything else -> :class:`~trader_core.feed.ScreenerFeedClient` (the
      external BC-250 screener JSON at ``feed_url``)
    """
    cfg = config or Config()
    if str(getattr(cfg, "feed_source", "url")).strip().lower() == "rank":
        return RankFeedClient(bridge, cfg)
    from .feed import ScreenerFeedClient  # local import keeps the url path lazy

    return ScreenerFeedClient(cfg.feed_url, timeout=cfg.request_timeout)


def default_params_path() -> str:
    """Return the default ``strategy_params.json`` path (repo root)."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "strategy_params.json")
