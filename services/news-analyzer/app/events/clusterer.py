"""Event clustering for news intelligence (Phase 3).

Groups ``news_event_extraction`` rows into event clusters by
``(stock_code, event_type, date)`` + 2-hour time bucket + core_event_text
token Jaccard similarity (>= 0.5). Pure computation, no LLM/DB dependency.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Dict, Optional

# Jaccard similarity threshold above which two core_event_texts are merged.
JACCARD_THRESHOLD = 0.5

# Tokenize on non-alphanumeric boundaries (Korean/ASCII/CJK aware).
_TOKEN_RE = re.compile(r"[^\w]+", re.UNICODE)


@dataclass
class EventCluster:
    stock_code: str
    event_type: str
    event_date: date
    time_bucket: str
    cluster_key: str
    article_count: int = 0
    first_article_at: Optional[datetime] = None
    last_article_at: Optional[datetime] = None
    total_importance: float = 0.0
    max_sentiment_abs: float = 0.0
    representative_core_event_text: str = ""
    _representative_importance: float = field(default=0.0, repr=False)


def _tokens(text: str) -> set:
    """Tokenize text into a set of lowercase tokens."""
    if not text:
        return set()
    return {t.lower() for t in _TOKEN_RE.split(text) if t}


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _time_bucket(dt: datetime) -> str:
    """2-hour bucket label within a day, e.g. ``14-16`` for 14:30."""
    hour = (dt.hour // 2) * 2
    return f"{hour:02d}-{hour + 2:02d}"


def _cluster_key(stock_code: str, event_type: str, event_date: date,
                 time_bucket: str, seq: int) -> str:
    """Unique cluster key. ``seq`` disambiguates multiple clusters in one bucket."""
    base = f"{stock_code}:{event_type}:{event_date}:{time_bucket}"
    return base if seq == 0 else f"{base}:{seq}"


def _new_cluster(ev: Dict) -> EventCluster:
    """Create a single-event cluster from an extraction row."""
    dt = ev["created_at"]
    bucket = _time_bucket(dt)
    return EventCluster(
        stock_code=ev["stock_code"],
        event_type=ev["event_type"],
        event_date=dt.date(),
        time_bucket=bucket,
        cluster_key=_cluster_key(
            ev["stock_code"], ev["event_type"], dt.date(), bucket, 0
        ),
        article_count=1,
        first_article_at=dt,
        last_article_at=dt,
        total_importance=float(ev.get("importance") or 0.0),
        max_sentiment_abs=abs(float(ev.get("sentiment_score") or 0.0)),
        representative_core_event_text=ev.get("core_event_text") or "",
        _representative_importance=float(ev.get("importance") or 0.0),
    )


def _can_merge(cl: EventCluster, ev: Dict) -> bool:
    """True if ``ev`` belongs to cluster ``cl``."""
    dt = ev["created_at"]
    if cl.stock_code != ev["stock_code"]:
        return False
    if cl.event_type != ev["event_type"]:
        return False
    if cl.event_date != dt.date():
        return False
    if cl.time_bucket != _time_bucket(dt):
        return False
    sim = _jaccard(
        _tokens(cl.representative_core_event_text),
        _tokens(ev.get("core_event_text") or ""),
    )
    return sim >= JACCARD_THRESHOLD


def _merge(cl: EventCluster, ev: Dict) -> None:
    """Merge ``ev`` into ``cl``, updating aggregates."""
    dt = ev["created_at"]
    importance = float(ev.get("importance") or 0.0)
    sentiment_abs = abs(float(ev.get("sentiment_score") or 0.0))

    cl.article_count += 1
    if cl.first_article_at is None or dt < cl.first_article_at:
        cl.first_article_at = dt
    if cl.last_article_at is None or dt > cl.last_article_at:
        cl.last_article_at = dt
    cl.total_importance += importance
    if sentiment_abs > cl.max_sentiment_abs:
        cl.max_sentiment_abs = sentiment_abs
    if importance > cl._representative_importance:
        cl._representative_importance = importance
        cl.representative_core_event_text = ev.get("core_event_text") or ""


def cluster(events: List[Dict]) -> List[EventCluster]:
    """Cluster extraction rows into event clusters.

    ``events``: list of dicts with keys ``stock_code``, ``event_type``,
    ``created_at`` (datetime), ``importance``, ``sentiment_score``,
    ``core_event_text``.
    """
    clusters: List[EventCluster] = []
    for ev in sorted(events, key=lambda e: e["created_at"]):
        merged = False
        for cl in clusters:
            if _can_merge(cl, ev):
                _merge(cl, ev)
                merged = True
                break
        if not merged:
            clusters.append(_new_cluster(ev))
    return clusters
