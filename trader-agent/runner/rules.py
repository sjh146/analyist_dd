"""Pure decision helpers for the R1-R4 market-hours rules.

Each rule is a small, side-effect-free function so the semantics are unit
testable without a bridge, feed or state file.  The loop orchestrates these
helpers; none of them talk to IO.

* R1 (regime proxy / candidate distribution): for each traded screener that
  actually produced candidates, average the top-N scores; when that average
  drops below the per-screener threshold every candidate of that screener is
  blocked for the cycle.
* R2 (entry limit): no new entries once today's buy-fill count reaches the cap
  (positions are still monitored - that part lives in the loop).
* R3 (intraday price deviation): a plan whose planned price moved more than
  ``max_pct`` from the live quote is dropped.  A missing/non-positive quote
  fails closed (candidate is skipped, never traded blind).
* R4 (daily loss limit): when equity falls below ``start_equity * (1-pct)``
  the whole account is liquidated and the loop halts.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from trader_core.feed import Candidate, FeedSnapshot, candidate_change_pct

KST = timezone(timedelta(hours=9))


def _as_float(value: Any) -> Optional[float]:
    """Return *value* as a float, or None when it is not numeric."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    """Return *value* as an int, or None when it is not numeric."""
    number = _as_float(value)
    if number is None:
        return None
    return int(number)


def _parse_iso(text: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, returning None when it is unusable."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(text: str) -> Optional[date]:
    """Parse ``YYYY-MM-DD`` (or an ISO timestamp), returning None on failure."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    parsed = _parse_iso(cleaned)
    if parsed is not None:
        return parsed.date()
    try:
        return datetime.strptime(cleaned[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _numeric_scores(candidates: List[Candidate]) -> List[float]:
    """Return the numeric, positive-or-zero scores present on the candidates."""
    scores: List[float] = []
    for candidate in candidates:
        try:
            value = float(candidate.score)
        except (TypeError, ValueError):
            continue
        scores.append(value)
    return scores


def top_n_average(candidates: List[Candidate], n: int = 10) -> Optional[float]:
    """Average of the top-``n`` candidate scores (desc), or None when empty.

    Candidates whose score is missing/unparseable are ignored for ranking.
    Fewer than ``n`` usable scores means all of them are averaged.
    """
    scores = _numeric_scores(candidates)
    if not scores:
        return None
    scores.sort(reverse=True)
    top = scores[: max(1, int(n))]
    return sum(top) / len(top)


def _declared_score_kind(candidates: List[Candidate]) -> str:
    """Return the score scale the feed declared for a candidate list.

    The publisher says what ``score`` *means* (``score_kind``): a raw screener
    score (``"screener"``) or a model probability x 100
    (``"calibrated_prob"``).  Without that distinction a single threshold
    silently closes a whole strategy (measured 2026-09-29: swing scores were
    model probabilities 0.50-0.62 while the threshold was 75 on a 0-100 scale).
    Mixed/absent declarations fall back to ``"screener"``.
    """
    kinds = []
    for candidate in candidates:
        extras = getattr(candidate, "extras", {}) or {}
        raw = getattr(candidate, "score_kind", None)
        if not raw:
            raw = extras.get("score_kind")
        text = str(raw or "").strip().lower()
        if text:
            kinds.append(text)
    if not kinds:
        return "screener"
    prob = sum(1 for kind in kinds if kind in ("calibrated_prob", "prob", "probability"))
    if prob * 2 > len(kinds):
        return "calibrated_prob"
    rank = sum(1 for kind in kinds if kind in ("rank_pct", "percentile", "rank"))
    if rank * 2 > len(kinds):
        return "rank_pct"
    return "screener"


def stale_candidates(
    snapshot: FeedSnapshot, cfg: Any, now: Optional[datetime] = None
) -> Dict[str, str]:
    """Return ``{code: reason}`` for candidates whose own signal went stale.

    Gate 1 only checks the snapshot's ``generated_at``, so a freshly published
    file could still carry days-old per-stock signals (measured 2026-09-29: a
    close candidate detected on 09-23 was still tradeable on 09-29).  Two
    independent bounds apply, whichever is tighter:

    * ``valid_until`` (ISO-8601, publisher-declared) -- passed once ``now`` is
      beyond it;
    * ``candidate_max_age_days[screener]`` -- the age of ``signal_date`` in
      calendar days.  0/absent in the mapping disables the age check.

    Only the offending candidates are dropped; the rest of the list is kept.
    """
    if now is None:
        now = datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    age_limits = getattr(cfg, "candidate_max_age_days", None) or {}
    result: Dict[str, str] = {}
    for screener, candidates in (getattr(snapshot, "candidates", {}) or {}).items():
        limit = _as_int(age_limits.get(screener))
        for candidate in candidates or []:
            extras = getattr(candidate, "extras", {}) or {}
            raw_until = getattr(candidate, "valid_until", None) or extras.get("valid_until")
            if raw_until:
                try:
                    until = _parse_iso(str(raw_until))
                except (TypeError, ValueError):
                    until = None
                if until is not None and until.tzinfo is None:
                    until = until.replace(tzinfo=KST)
                if until is not None and now > until:
                    result[candidate.stock_code] = "stale: valid_until {0} passed".format(
                        until.strftime("%Y-%m-%d %H:%M")
                    )
                    continue
            if not limit or limit <= 0:
                continue
            raw_date = getattr(candidate, "signal_date", None) or extras.get("signal_date")
            signal_date = _parse_date(str(raw_date)) if raw_date else None
            if signal_date is None:
                continue
            age_days = (now.astimezone(KST).date() - signal_date).days
            if age_days > limit:
                result[candidate.stock_code] = (
                    "stale: signal_date {0} is {1}d old (> {2}d)".format(
                        signal_date.isoformat(), age_days, limit
                    )
                )
    return result


def apply_r1(snapshot: FeedSnapshot, cfg: Any) -> Dict[str, str]:
    """Return ``{screener: reason}`` for every traded screener R1 blocks.

    A screener is checked only when it appears in ``cfg.screeners`` AND has a
    threshold.  Which threshold applies follows the scale the feed declared
    (``score_kind``): ``r1_min_avg_prob`` for ``calibrated_prob``,
    ``r1_min_avg_rank_pct`` for ``rank_pct``, otherwise the 0-100
    ``r1_min_avg_score`` (which a ``rank_pct`` feed falls back to - both are
    0-100).  Screeners with no candidates are skipped (nothing to judge).  An
    empty result means R1 blocks nothing.
    """
    thresholds = getattr(cfg, "r1_min_avg_score", {})
    prob_thresholds = getattr(cfg, "r1_min_avg_prob", {}) or {}
    rank_thresholds = getattr(cfg, "r1_min_avg_rank_pct", {}) or {}
    top_n = int(getattr(cfg, "r1_top_n", 10))
    screeners = list(getattr(cfg, "screeners", []))
    blocked: Dict[str, str] = {}
    for screener in screeners:
        if (screener not in thresholds and screener not in prob_thresholds
                and screener not in rank_thresholds):
            continue
        candidates = snapshot.candidates.get(screener, [])
        if not candidates:
            continue
        average = top_n_average(candidates, top_n)
        if average is None:
            continue  # no numeric scores -> engine's signal gate will handle
        kind = _declared_score_kind(candidates)
        if kind == "calibrated_prob" and screener in prob_thresholds:
            probability = average / 100.0
            threshold = float(prob_thresholds[screener])
            if probability < threshold:
                blocked[screener] = "r1: avg prob {0:.3f} < {1:.2f} (calibrated)".format(
                    probability, threshold
                )
            continue
        if kind == "rank_pct" and screener in rank_thresholds:
            threshold = float(rank_thresholds[screener])
            if average < threshold:
                blocked[screener] = "r1: avg rank_pct {0:.1f} < {1:.1f} (percentile)".format(
                    average, threshold
                )
            continue
        if screener not in thresholds:
            continue
        threshold = float(thresholds[screener])
        if average < threshold:
            blocked[screener] = "r1: avg score {0:.1f} < {1:.1f}".format(
                average, threshold
            )
    return blocked


def apply_r2(entry_count: int, max_entries_per_day: int) -> Tuple[bool, str]:
    """Return ``(blocked, reason)`` for today's entry cap.

    ``blocked`` is True when *entry_count* already reached *max_entries_per_day*
    so no new entries may be taken this cycle (monitoring continues).
    """
    count = int(entry_count or 0)
    cap = int(max_entries_per_day or 0)
    if cap <= 0:
        return True, "r2: no new entries (max {0})".format(cap)
    if count >= cap:
        return True, "r2: entries today {0} >= max {1}".format(count, cap)
    return False, ""


def r3_deviation_ok(
    close_price: Optional[float], quote_price: Optional[float], max_pct: float
) -> bool:
    """Return True when a plan's *close_price* is close to the live *quote*.

    A missing/non-positive quote or plan price fails closed (False) so the
    candidate is skipped instead of being traded blind.
    """
    try:
        plan_price = float(close_price)
        live = float(quote_price)
    except (TypeError, ValueError):
        return False
    if plan_price <= 0 or live <= 0:
        return False
    deviation = abs(live - plan_price) / plan_price
    return deviation <= float(max_pct)


def r3_reason(
    close_price: Optional[float], quote_price: Optional[float], max_pct: float
) -> str:
    """Return a human-readable R3 skip reason for a rejected candidate."""
    try:
        plan_price = float(close_price)
        live = float(quote_price)
    except (TypeError, ValueError):
        plan_price = None
        live = None
    if live is None or live <= 0:
        return "r3: no quote"
    if plan_price is None or plan_price <= 0:
        return "r3: no plan price"
    return "r3: price moved {0:.1%}".format(abs(live - plan_price) / plan_price)


def r4_triggered(
    start_equity: Optional[float], equity_now: Optional[float], loss_pct: float
) -> bool:
    """Return True when the account crossed the daily equity loss limit.

    *start_equity* is the day's opening equity recorded at the first successful
    CHECK; a missing/non-positive baseline never triggers (no floor to judge).
    """
    try:
        baseline = float(start_equity)
        current = float(equity_now)
    except (TypeError, ValueError):
        return False
    if baseline <= 0 or current < 0:
        return False
    return current < baseline * (1.0 - float(loss_pct))


def heat_blocks(
    snapshot: Any, config: Any
) -> Dict[str, str]:
    """Return ``{screener: reason}`` for candidate sets that look *overheated*.

    ``r1_min_avg_score`` only ever asks "is this list strong enough?".  A
    momentum list can be strong and *late* at the same time: on 2026-09-10 the
    candidates averaged 93.7 score and +19.7 % on the day, and the entries taken
    from that list lost 31,455 KRW between them.  These ceilings are the mirror
    image -- above them the list is buying the top, not the trend.

    Both ceilings are opt-in (0 disables) and use the same top-N average as R1.
    ``avg_pct`` comes from the candidate's day change (``pct_change`` on the rank
    feed, ``day_change_pct`` on the screener feed — see
    :func:`trader_core.feed.candidate_change_pct`); a feed that sends neither
    leaves the pct ceiling silent.
    """
    result: Dict[str, str] = {}
    top_n = int(getattr(config, "r1_top_n", 10) or 10)
    max_score = float(getattr(config, "r1_max_avg_score", 0.0) or 0.0)
    max_pct = float(getattr(config, "r1_max_avg_pct", 0.0) or 0.0)
    if max_score <= 0 and max_pct <= 0:
        return result
    for screener, candidates in (getattr(snapshot, "candidates", {}) or {}).items():
        items = list(candidates or [])
        if not items:
            continue
        if max_score > 0:
            scores = [float(c.score) for c in items[:top_n] if getattr(c, "score", None) is not None]
            if scores:
                average = sum(scores) / len(scores)
                if average > max_score:
                    result[screener] = "heat: avg score {0:.1f} > {1:.1f}".format(
                        average, max_score
                    )
                    continue
        if max_pct > 0:
            pcts = []
            for candidate in items[:top_n]:
                value = candidate_change_pct(candidate)
                if value is not None:
                    pcts.append(value)
            if pcts:
                average_pct = sum(pcts) / len(pcts)
                if average_pct > max_pct:
                    result[screener] = "heat: avg +{0:.1f}% > +{1:.1f}%".format(
                        average_pct, max_pct
                    )
    return result


def gate_blocked_codes(payload: Any) -> Dict[str, str]:
    """Return ``{code: reason}`` for every code the news gate vetoes.

    Payload shape (BC-250 ``/newsgate``):
    ``{"gate": {code: {"verdict": "block"|"ok"|"unknown", "reasons": [...]}}}``
    Codes with verdict ``"block"`` are returned; anything else is ignored.
    A missing/odd payload yields an empty dict (caller decides fail-open/closed).
    """
    blocked: Dict[str, str] = {}
    if not isinstance(payload, dict):
        return blocked
    gate = payload.get("gate")
    if not isinstance(gate, dict):
        return blocked
    for code, entry in gate.items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("verdict", "")).lower() != "block":
            continue
        reasons = entry.get("reasons")
        detail = ""
        if isinstance(reasons, list) and reasons:
            detail = "r5: " + "; ".join(str(r) for r in reasons if str(r))[:200]
        blocked[str(code)] = detail or "r5: news veto"
    return blocked
