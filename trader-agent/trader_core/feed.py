"""BC-250 screener feed client and typed snapshot.

The feed (``screener_latest.json``) carries three candidate lists
(``close``, ``swing``, ``daytrading``) where every value is a STRING and the
top-level ``generated_at`` is a timezone-aware ISO timestamp in ``+09:00``.
``scoring_summary.screener_stats`` can be an empty ``{}`` and must never
crash parsing (Kelly priors then fall back to config).

This module is stdlib-only (``urllib.request``) and parses defensively: empty
strings become ``None``, malformed numbers become ``None``, non-6-digit stock
codes are dropped, and anything unusable raises a typed :class:`FeedError`.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from . import net
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

KST = timezone(timedelta(hours=9))

_PLAIN_CODE_RE = re.compile(r"^[0-9]{6}$")
_PREFIXED_CODE_RE = re.compile(r"^A[0-9]{6}$")

# Core per-item keys lifted into Candidate attributes.  Anything else on an
# item is preserved verbatim in ``extras``.
_CLOSE_CORE = frozenset(
    {
        "rank", "stock_code", "stock_name", "sector", "signal_date",
        "close_price", "score", "reason",
        # contract v1.1 model metadata: first-class so rules/engine can act on
        # them instead of digging through ``extras`` (2026-09-29).
        "score_kind", "valid_until", "ml_prob", "ml_horizon_days", "ml_model",
        "ml_auc",
    }
)


class FeedError(Exception):
    """Raised when the screener payload is unparseable or malformed."""


def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def candidate_change_pct(candidate: Any) -> Optional[float]:
    """Return a candidate's day change in percent, or ``None``.

    Two producers exist and they name the same quantity differently: the rank
    feed publishes ``pct_change`` (its own diff-based figure) and the screener
    feed publishes ``day_change_pct``.  Both land in ``Candidate.extras``
    verbatim, so a consumer that reads only one name silently loses the value
    for the other feed — measured 2026-09-30: the heat ceiling read
    ``pct_change`` while the screener feed sent ``day_change_pct``, so the
    ``r1_max_avg_pct`` guard never fired on a close list averaging +20.2 % on
    the day, the exact profile that guard was built for.
    """
    extras = getattr(candidate, "extras", None) or {}
    for key in ("pct_change", "day_change_pct"):
        value = _parse_float(extras.get(key))
        if value is not None:
            return value
    return None


def _parse_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def normalize_code(value: Any) -> Optional[str]:
    """Return a plain 6-digit code or ``None`` when *value* is not a code."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if _PLAIN_CODE_RE.match(text):
        return text
    if _PREFIXED_CODE_RE.match(text):
        return text[1:]
    return None


def parse_timestamp(value: Any) -> datetime:
    """Parse a feed timestamp into a timezone-aware datetime (KST if naive).

    :raises FeedError: when the value cannot be parsed as ISO 8601.
    """
    if value is None:
        raise FeedError("missing timestamp")
    text = str(value).strip()
    if text == "":
        raise FeedError("missing timestamp")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise FeedError("unparseable timestamp: {0!r}".format(text))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed


@dataclass
class Candidate:
    """A single typed screener candidate (parsed from string-typed JSON)."""

    rank: Optional[int]
    stock_code: str
    stock_name: str = ""
    sector: str = ""
    signal_date: Optional[str] = None
    close_price: Optional[float] = None
    score: Optional[float] = None
    reason: str = ""
    # --- contract v1.1 (2026-09-29) -------------------------------------- #
    # ``score_kind`` tells the rules what ``score`` means: a raw screener
    # score or a calibrated model probability x 100.  ``valid_until`` is the
    # publisher's own expiry for this candidate (ISO-8601); ``ml_*`` carries
    # the model metadata the analyzer actually declared (never inferred).
    score_kind: str = ""
    valid_until: Optional[str] = None
    ml_prob: Optional[float] = None
    ml_horizon_days: Optional[int] = None
    ml_model: str = ""
    ml_auc: Optional[float] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FeedSnapshot:
    """A parsed, immutable feed snapshot."""

    generated_at: datetime
    source: str = ""
    candidates: Dict[str, List[Candidate]] = field(default_factory=dict)
    scoring_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Which filters/scoring produced this snapshot.  The rank feed fills it in
    # so every trade can record the algorithm that chose it; an external feed
    # may describe its own pipeline here (optional).
    filters: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    def is_fresh(self, now: datetime, max_age: float) -> bool:
        """Return True when the snapshot is newer than ``max_age`` seconds.

        ``now`` should be timezone-aware; a naive ``now`` is assumed to be
        KST.  A payload stamped slightly in the future counts as fresh.
        """
        now = now if now.tzinfo is not None else now.replace(tzinfo=KST)
        age = now - self.generated_at
        return age.total_seconds() <= max_age

    def stats_for(self, screener: str) -> Dict[str, Any]:
        """Return the scoring_stats sub-dict for one screener (empty-safe)."""
        entry = self.scoring_stats.get(screener)
        if isinstance(entry, dict):
            return dict(entry)
        return {}

    # ------------------------------------------------------------------ #
    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "FeedSnapshot":
        """Parse a decoded JSON payload into a :class:`FeedSnapshot`."""
        if not isinstance(payload, dict):
            raise FeedError("feed payload must be a JSON object")
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, dict):
            raise FeedError("feed payload has no candidates object")
        generated_at = parse_timestamp(payload.get("generated_at"))

        parsed_candidates: Dict[str, List[Candidate]] = {}
        for name, container in raw_candidates.items():
            items = container.get("items") if isinstance(container, dict) else None
            if not isinstance(items, list):
                parsed_candidates[str(name)] = []
                continue
            parsed_list: List[Candidate] = []
            for raw_item in items:
                if not isinstance(raw_item, dict):
                    continue
                candidate = _parse_candidate(raw_item)
                if candidate is not None:
                    parsed_list.append(candidate)
            parsed_candidates[str(name)] = parsed_list

        scoring_stats: Dict[str, Any] = {}
        scoring_summary = payload.get("scoring_summary")
        if isinstance(scoring_summary, dict):
            raw_stats = scoring_summary.get("screener_stats", {})
            if isinstance(raw_stats, dict):
                scoring_stats = dict(raw_stats)

        raw_filters = payload.get("filters")
        filters = dict(raw_filters) if isinstance(raw_filters, dict) else {}

        return cls(
            generated_at=generated_at,
            source=str(payload.get("source", "") or ""),
            candidates=parsed_candidates,
            scoring_stats=scoring_stats,
            filters=filters,
        )

    @classmethod
    def from_text(cls, raw: str) -> "FeedSnapshot":
        """Parse a raw JSON string into a :class:`FeedSnapshot`."""
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise FeedError("feed payload is not valid JSON: {0}".format(exc))
        return cls.from_payload(payload)


def _parse_candidate(raw_item: Dict[str, Any]) -> Optional[Candidate]:
    """Parse one item dict, or return None when the stock code is unusable."""
    code = normalize_code(raw_item.get("stock_code"))
    if code is None:
        return None
    core = {key: raw_item.get(key) for key in _CLOSE_CORE}
    extras = {
        key: raw_item.get(key)
        for key in raw_item.keys()
        if key not in _CLOSE_CORE and not key.startswith("_")
    }
    return Candidate(
        rank=_parse_int(core.get("rank")),
        stock_code=code,
        stock_name=str(core.get("stock_name") or ""),
        sector=str(core.get("sector") or ""),
        signal_date=str(core.get("signal_date")) if core.get("signal_date") else None,
        close_price=_parse_float(core.get("close_price")),
        score=_parse_float(core.get("score")),
        reason=str(core.get("reason") or ""),
        score_kind=str(core.get("score_kind") or "").strip(),
        valid_until=str(core.get("valid_until")) if core.get("valid_until") else None,
        ml_prob=_parse_float(core.get("ml_prob")),
        ml_horizon_days=_parse_int(core.get("ml_horizon_days")),
        ml_model=str(core.get("ml_model") or ""),
        ml_auc=_parse_float(core.get("ml_auc")),
        extras=extras,
    )


class ScreenerFeedClient:
    """Polls the BC-250 screener JSON feed over HTTP (stdlib only)."""

    def __init__(
        self, feed_url: str, timeout: float = 10.0,
        accept_local_path: bool = True,
    ) -> None:
        """Construct a client for *feed_url*.

        When *accept_local_path* is True and *feed_url* has no URL scheme it
        is treated as a local file path (used by the CLI for offline scans and
        by tests; keeps the laptop usable without the tailscale link).
        """
        self.feed_url = feed_url
        self.timeout = timeout
        self.accept_local_path = accept_local_path

    def fetch(self) -> FeedSnapshot:
        """GET the feed and return a parsed :class:`FeedSnapshot`.

        :raises FeedError: on transport failure, HTTP error or malformed body.
        """
        if self.accept_local_path and "://" not in self.feed_url:
            return self.fetch_file(self.feed_url)
        try:
            with net.urlopen(self.feed_url, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise FeedError("feed fetch failed: {0}".format(exc.reason or exc))
        except OSError as exc:
            raise FeedError("feed fetch failed: {0}".format(exc))
        return FeedSnapshot.from_text(raw)

    def fetch_file(self, path: str) -> FeedSnapshot:
        """Read a local JSON feed file (test/offline helper)."""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            raise FeedError("feed file unreadable: {0}".format(exc))
        return FeedSnapshot.from_text(raw)
