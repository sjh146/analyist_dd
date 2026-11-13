"""Journal -> parameters: the self-tuning review step.

This is the piece that was **absent entirely** before (``INTEGRATION_MAP.md``
§8b-iii): the journal recorded every decision and trade, but nothing ever read
it back to change behaviour.  :func:`review` closes that loop.

What it does, honestly and conservatively:

1. Read the closed trades in a rolling window from the journal.
2. Compute realised performance (win rate, mean win/loss as **percentage
   return on the position**, profit factor).
3. Shrink the observed statistics toward conservative priors with a
   pseudo-count, so three lucky trades cannot move Kelly sizing much.
4. Adjust the entry bar (``r1_min_avg_score``) in small bounded steps, and only
   once a minimum sample size exists.
5. Write ``strategy_params.json``, which both sides read live:
   * engine side -- ``trader_core.rankfeed`` reads the Kelly priors and the
     candidate filters on every ``fetch()``;
   * loop side -- ``runner.config.RunnerConfig.apply_params`` reads
     ``screeners`` / ``r1_min_avg_score`` at startup.

No file is written when there is nothing to learn yet (no closed trades), so a
fresh system keeps trading on the documented defaults.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .config import KST

# Shrinkage: observed stats are blended with these priors using
# ``k`` pseudo-trades, so small samples barely move the sizing.
SHRINKAGE_PSEUDO_COUNT = 10.0
PRIOR_WIN_RATE = 0.55
PRIOR_AVG_WIN_PCT = 2.0
PRIOR_AVG_LOSS_PCT = 1.5

# Bounded, small steps for the R1 entry bar.
THRESHOLD_MIN = 40.0
THRESHOLD_MAX = 85.0
STEP_TIGHTEN = 5.0   # weak realised win rate -> demand better candidates
STEP_LOOSEN = 2.5    # healthy realised win rate -> accept slightly weaker ones
WEAK_WIN_RATE = 0.40
HEALTHY_WIN_RATE = 0.55
MIN_CLOSED_TRADES = 5

# Percentile scoring rates candidates inside their own snapshot, so a shortlist
# that *is* the candidate set (the self-generated rank feed) averages ~50 no
# matter how good the list is (measured 2026-09-11: 84/58/50/44/40/24 -> 50.0).
# A floor raised to that mean does not raise the bar -- it blocks the feed on
# every cycle -- so tightening stops short of it whenever the current floor
# already sits below the mean.
STRUCTURAL_SCORE_MEAN = 50.0


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def trade_return_pct(row: Dict[str, Any]) -> Optional[float]:
    """Return a closed trade's return as a percentage of the position cost."""
    pnl = _as_float(row.get("pnl"))
    price = _as_float(row.get("price"))
    qty = _as_float(row.get("qty"))
    if pnl is None or not price or not qty:
        return None
    cost = abs(price * qty)
    if cost <= 0:
        return None
    return pnl / cost * 100.0


def performance(closed: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate closed trades into review statistics.

    Returns ``{trades, wins, losses, win_rate, avg_win_pct, avg_loss_pct,
    total_pnl, profit_factor}``; ``profit_factor`` is gross win / gross loss
    (``None`` when there are no losses).
    """
    returns: List[float] = []
    total_pnl = 0.0
    for row in closed:
        value = trade_return_pct(row)
        row_pnl = _as_float(row.get("pnl"))
        if row_pnl is not None:
            total_pnl += row_pnl
        if value is not None:
            returns.append(value)
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(returns),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(returns)) if returns else 0.0,
        "avg_win_pct": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss_pct": gross_loss / len(losses) if losses else 0.0,
        "total_pnl": total_pnl,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
    }


def shrink(observed: float, prior: float, samples: int,
           k: float = SHRINKAGE_PSEUDO_COUNT) -> float:
    """Blend *observed* toward *prior* using *k* pseudo-samples."""
    n = max(0.0, float(samples))
    if n <= 0:
        return prior
    return (n * observed + k * prior) / (n + k)


def recommend(
    params: Dict[str, Any],
    stats: Dict[str, Any],
    screener: str = "rank_momentum",
    min_samples: int = MIN_CLOSED_TRADES,
) -> Tuple[Dict[str, Any], List[str]]:
    """Return ``(updated_params, rationale)`` for one review cycle.

    Never raises and never returns a params dict that is *less* conservative
    than the defaults: the entry bar only moves inside
    ``THRESHOLD_MIN..THRESHOLD_MAX`` and the priors are always shrunk.
    """
    updated = dict(params)
    rationale: List[str] = []
    trades = int(stats.get("trades") or 0)
    win_rate = float(stats.get("win_rate") or 0.0)

    if trades >= max(1, int(min_samples)):
        prior_win = shrink(win_rate, PRIOR_WIN_RATE, trades)
        prior_win = min(max(prior_win, 0.05), 0.95)
        avg_win = shrink(
            float(stats.get("avg_win_pct") or 0.0), PRIOR_AVG_WIN_PCT, trades
        )
        avg_loss = shrink(
            float(stats.get("avg_loss_pct") or 0.0), PRIOR_AVG_LOSS_PCT, trades
        )
        updated["prior_win_rate"] = round(prior_win, 4)
        updated["prior_avg_win_pct"] = round(max(avg_win, 0.1), 4)
        updated["prior_avg_loss_pct"] = round(max(avg_loss, 0.1), 4)
        rationale.append(
            "priors: win_rate {0:.3f} -> {1:.3f} (shrunk, n={2})".format(
                win_rate, prior_win, trades
            )
        )

        thresholds = dict(updated.get("r1_min_avg_score") or {})
        current = _as_float(thresholds.get(screener))
        if current is None:
            from .rankfeed import DEFAULT_R1_MIN_AVG_SCORE

            current = DEFAULT_R1_MIN_AVG_SCORE
        new_value = current
        if win_rate < WEAK_WIN_RATE:
            ceiling = THRESHOLD_MAX
            if current < STRUCTURAL_SCORE_MEAN:
                # A self-scored shortlist cannot clear a floor at its own mean.
                ceiling = min(ceiling, STRUCTURAL_SCORE_MEAN - STEP_TIGHTEN)
            new_value = min(ceiling, current + STEP_TIGHTEN)
            rationale.append(
                "entry bar: win rate {0:.2f} < {1:.2f} -> tighten {2:.1f} -> {3:.1f}".format(
                    win_rate, WEAK_WIN_RATE, current, new_value
                )
            )
        elif win_rate >= HEALTHY_WIN_RATE and float(stats.get("total_pnl") or 0.0) > 0:
            new_value = max(THRESHOLD_MIN, current - STEP_LOOSEN)
            rationale.append(
                "entry bar: win rate {0:.2f} healthy and PnL positive -> loosen "
                "{1:.1f} -> {2:.1f}".format(win_rate, current, new_value)
            )
        else:
            rationale.append(
                "entry bar: win rate {0:.2f} unchanged at {1:.1f}".format(
                    win_rate, current
                )
            )
        thresholds[screener] = round(new_value, 2)
        updated["r1_min_avg_score"] = thresholds
        updated.setdefault("screeners", [screener])
    else:
        rationale.append(
            "not enough closed trades ({0} < {1}) - parameters unchanged".format(
                trades, max(1, int(min_samples))
            )
        )
    return updated, rationale


def load_params(path: str) -> Dict[str, Any]:
    """Read a params file (missing/malformed -> empty dict)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_params(path: str, params: Dict[str, Any]) -> str:
    """Write *params* as JSON (atomically) and return the path."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(params, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)
    return path


def review(
    journal: Any,
    params_path: str,
    screener: str = "rank_momentum",
    period_days: int = 30,
    min_samples: int = MIN_CLOSED_TRADES,
    apply: bool = True,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run one review cycle; optionally write the params file.

    Returns ``{stats, params, rationale, written, params_path}`` so callers
    (and tests) can inspect exactly what was decided.
    """
    closed = journal.closed_trades(period_days=period_days, now=now) if journal else []
    stats = performance(closed)
    params = load_params(params_path)
    updated, rationale = recommend(
        params, stats, screener=screener, min_samples=min_samples
    )
    updated["generated_at"] = datetime.now(KST).isoformat()
    updated["reviewed_trades"] = stats["trades"]
    written = ""
    if apply and stats["trades"] > 0:
        written = write_params(params_path, updated)
    return {
        "stats": stats,
        "params": updated,
        "rationale": rationale,
        "written": written,
        "params_path": params_path,
    }
