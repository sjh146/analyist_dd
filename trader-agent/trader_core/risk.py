"""Pure risk mathematics for trader-core.

All formulas are ported from the legacy BC-250 assets (READ-ONLY references
from PHASE3_BRIEF.md):

* ``order_sizer.py`` ``KellySizer`` — ``f* = p - q/b`` with a 0.25 cap.
* ``strategy-agents .../position_sizer.py`` ``calculate_kelly_fraction``.
* ``stop_loss.py`` ``StopLoss`` — SL/TP ratio checks + trailing stop.
* ``balance_checker.py`` / ``position_checker.py`` — buyable / concentration
  guards (their Redis/PostgreSQL storage is replaced by pure in-memory math).
* trade-executor CWE-20 defence: a market order needs a recent reference
  price (or an enforced sanity band).

Everything here is a pure function: no IO, no global state, so it is trivially
unit-testable on Linux and Windows alike.

Formula contract (implemented exactly):

* ``f* = p - (1 - p)/b`` where ``b = avg_win / avg_loss``.
* Guards: ``avg_loss <= 0``, ``avg_win <= 0``, ``p <= 0``, ``p >= 1`` all
  return ``0.0`` (mirrors ``calculate_kelly_fraction``).
* ``sized_qty`` deploys ``kelly_fraction * f*`` (fractional Kelly) capped at
  ``kelly_cap`` (0.25), then bounded by ``max_position_pct`` and
  ``max_order_amount``; share quantity is floored to whole shares and returns
  0 when it rounds below 1 share.
"""

from __future__ import annotations

from typing import Dict, Optional, Union

STOP = "stop"
TAKE_PROFIT = "tp"


def volatility_stop_pct(
    day_range_pct: float,
    floor_pct: float = 0.07,
    factor: float = 0.5,
    cap_pct: float = 0.20,
) -> float:
    """Return a stop distance scaled to the name's volatility.

    A fixed 7 % stop sits *inside* the daily noise for a momentum list: the held
    names on 2026-09-10 had 19-32 % day ranges, so 7 % is 22-36 % of a normal
    swing (either tripped by noise or, as observed, left open to -17.8 %).

    ``stop = max(floor_pct, factor * day_range)``, capped at ``cap_pct``.
    A missing/zero range falls back to the floor.
    """
    floor = max(0.0, float(floor_pct or 0.0))
    try:
        range_fraction = float(day_range_pct) / 100.0
    except (TypeError, ValueError):
        range_fraction = 0.0
    if range_fraction <= 0:
        return floor
    stop = max(floor, float(factor or 0.0) * range_fraction)
    cap = float(cap_pct or 0.0)
    return min(stop, cap) if cap > 0 else stop


def risk_capped_qty(
    equity: float, price: float, stop_pct: float, risk_per_trade_pct: float
) -> int:
    """Shares such that ``price * stop_pct * qty <= equity * risk_per_trade_pct``.

    Equal-risk sizing: every position risks the same fraction of equity, so a
    volatile name (wide stop) automatically gets a smaller position.  Returns 0
    when the budget cannot buy a single share (caller treats 0 as "skip").
    """
    try:
        eq = float(equity)
        px = float(price)
        stop = float(stop_pct)
        budget_pct = float(risk_per_trade_pct)
    except (TypeError, ValueError):
        return 0
    if eq <= 0 or px <= 0 or stop <= 0 or budget_pct <= 0:
        return 0
    budget = eq * budget_pct
    per_share = px * stop
    if per_share <= 0:
        return 0
    return int(budget // per_share)


def kelly_fraction(
    win_rate: float, avg_win: float, avg_loss: float
) -> float:
    """Return the raw Kelly fraction ``f* = p - q/b``.

    ``b = avg_win / avg_loss``.  Guards: ``b <= 0``, ``win_rate <= 0`` and
    ``win_rate >= 1`` all yield ``0.0`` (so the result is always >= 0).  The
    value is NOT capped here; apply ``kelly_cap`` where the fraction is used.
    """
    p = win_rate
    if p <= 0.0 or p >= 1.0:
        return 0.0
    if avg_loss <= 0.0 or avg_win <= 0.0:
        return 0.0
    b = avg_win / avg_loss
    if b <= 0.0:
        return 0.0
    return max(0.0, p - (1.0 - p) / b)


def sized_qty(
    equity: float,
    price: float,
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    kelly_fraction: float = 1.0,
    kelly_cap: float = 0.25,
    max_position_pct: float = 0.20,
    max_order_amount: Optional[float] = None,
) -> tuple:
    """KellySizer-equivalent position sizing.

    :param equity: total account equity in KRW (fraction denominator).
    :param price: per-share reference price in KRW.
    :param win_rate: win probability ``p`` (feed stat or config prior).
    :param avg_win: average winning outcome (ratio or KRW; only the ratio to
        ``avg_loss`` matters).
    :param avg_loss: average losing outcome (must be > 0).
    :param kelly_fraction: fraction of the full-Kelly ``f*`` to deploy
        (fractional Kelly, clamped to ``[0, 1]``).
    :param kelly_cap: absolute cap on the deployed equity fraction.
    :param max_position_pct: per-stock cap as a fraction of ``equity``.
    :param max_order_amount: KRW ceiling for a single order (None = uncapped).

    Returns ``(qty, invest_amount)`` where ``invest_amount = qty * price``.
    Returns ``(0, 0)`` when the size rounds below one share or inputs are
    unusable (price <= 0, equity <= 0, zero Kelly).
    """
    if price <= 0 or equity <= 0:
        return (0, 0)
    f_star = _kelly(win_rate, avg_win, avg_loss)
    if f_star <= 0:
        return (0, 0)
    deploy = max(0.0, min(1.0, kelly_fraction))
    f_eff = min(f_star * deploy, max(0.0, kelly_cap))
    budget = equity * f_eff
    budget = min(budget, equity * max(0.0, max_position_pct))
    if max_order_amount is not None and max_order_amount > 0:
        budget = min(budget, float(max_order_amount))
    # Round the KRW budget to the nearest won, then floor to whole shares so
    # float noise never drops a share (e.g. 199.999... -> 200).
    budget_won = int(round(budget))
    price_won = int(round(price))
    if budget_won <= 0 or price_won <= 0:
        return (0, 0)
    qty = budget_won // price_won
    if qty < 1:
        return (0, 0)
    return (qty, qty * price_won)


def _kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Private raw Kelly used internally (avoids param shadowing)."""
    p = win_rate
    if p <= 0.0 or p >= 1.0:
        return 0.0
    if avg_loss <= 0.0 or avg_win <= 0.0:
        return 0.0
    b = avg_win / avg_loss
    if b <= 0.0:
        return 0.0
    return max(0.0, p - (1.0 - p) / b)


# ---------------------------------------------------------------------- #
# Stop-loss / take-profit
# ---------------------------------------------------------------------- #
def _entry_price(position: Dict) -> float:
    """Best-effort entry price lookup across legacy position key names."""
    for key in ("avg_price", "avg_buy_price", "entry_price", "buy_price"):
        value = position.get(key, 0)
        try:
            as_float = float(value)
        except (TypeError, ValueError):
            continue
        if as_float > 0:
            return as_float
    return 0.0


def should_stop(
    position: Dict,
    price: float,
    sl_pct: float = 0.07,
    tp_pct: float = 0.15,
) -> Optional[str]:
    """Return ``"stop"``, ``"tp"`` or ``None`` for a position vs current price.

    Uses the position entry/avg price.  A position whose entry price is
    unknown, or a non-positive current price, never triggers (fail open on
    missing data => hold, matching the legacy StopLoss).
    """
    entry = _entry_price(position)
    if entry <= 0:
        return None
    try:
        px = float(price)
    except (TypeError, ValueError):
        return None
    if px <= 0:
        return None
    pnl_pct = (px - entry) / entry
    if sl_pct and sl_pct > 0 and pnl_pct <= -sl_pct:
        return STOP
    if tp_pct and tp_pct > 0 and pnl_pct >= tp_pct:
        return TAKE_PROFIT
    return None


def check_trailing_stop(
    current_price: float, highest_price: float, trail_pct: float = 0.07
) -> bool:
    """Return True when *current_price* broke a trailing stop.

    Mirrors the legacy ``trailing_stop``: the reference high is the larger of
    the tracked high and the current price, the stop sits ``trail_pct`` below
    it, and the stop fires when ``current <= stop``.
    """
    high = max(highest_price, current_price)
    stop_price = high * (1.0 - trail_pct)
    return current_price <= stop_price


# ---------------------------------------------------------------------- #
# Balance / position / order guards
# ---------------------------------------------------------------------- #
def check_buyable(equity: float, buy_amount: float, reserve: float = 0.0) -> bool:
    """Return True when ``buy_amount + reserve`` fits within *equity*.

    ``equity`` is available buying power (cash).  ``reserve`` is an optional
    cash buffer to keep untouched.  Fail-closed: a non-positive buy amount or
    an unreadable equity answers False (no balance confirmation => reject).
    """
    try:
        amount = float(buy_amount)
        cash = float(equity)
        buf = max(0.0, float(reserve))
    except (TypeError, ValueError):
        return False
    if amount <= 0 or cash <= 0:
        return False
    return amount + buf <= cash


def check_open_positions(count: int, cap: int) -> bool:
    """Return True when another position may be opened.

    ``cap <= 0`` means unlimited.  Fails closed when *count* is unreadable.
    """
    try:
        limit = int(cap)
    except (TypeError, ValueError):
        return True
    if limit <= 0:
        return True
    try:
        held = int(count)
    except (TypeError, ValueError):
        return False  # cannot confirm -> refuse to add exposure
    return held < limit


def check_invested_ratio(
    invested: float, additional: float, equity: float, cap: float
) -> bool:
    """Return True when this order keeps total cost basis within *cap* of equity.

    ``cap <= 0`` disables the check.  Fails closed when equity is unreadable
    (that is what protects the account from a blind full-size buy).
    """
    try:
        ratio = float(cap)
    except (TypeError, ValueError):
        return True
    if ratio <= 0:
        return True
    try:
        base = float(invested or 0.0)
        add = float(additional or 0.0)
        total_equity = float(equity)
    except (TypeError, ValueError):
        return False
    if total_equity <= 0 or add <= 0:
        return False
    return (base + add) <= ratio * total_equity


def check_position_limit(
    held_qty: int,
    existing_qty: int,
    additional: int,
    max_position_pct: float,
    equity: float,
    price: float,
) -> bool:
    """Return True when adding *additional* shares keeps concentration legal.

    :param held_qty: shares already owned of this code.
    :param existing_qty: shares already committed via outstanding buy orders
        for the same code (so unfilled buys count toward the ceiling).
    :param additional: proposed new shares for this order.

    The combined ``held + existing + additional`` position value
    (``qty * price``) must not exceed ``max_position_pct * equity``.  Missing
    price/equity fails closed (cannot prove legality => reject), mirroring the
    legacy position checker's CWE-862 defence.
    """
    try:
        price_f = float(price)
        eq = float(equity)
    except (TypeError, ValueError):
        return False
    if price_f <= 0 or eq <= 0:
        return False
    if additional is None or int(additional) <= 0:
        return True
    total_qty = int(held_qty or 0) + int(existing_qty or 0) + int(additional)
    return (total_qty * price_f) <= max(0.0, max_position_pct) * eq


def check_daily_trade_count(count: int, daily_max: int) -> bool:
    """Return True when *count* (trades already taken today) < *daily_max*."""
    if daily_max is None or int(daily_max) <= 0:
        return False
    return int(count or 0) < int(daily_max)


# ---------------------------------------------------------------------- #
# CWE-20 market-order reference-price defence
# ---------------------------------------------------------------------- #
def validate_market_order(
    price: Optional[Union[float, int]],
    reference_price: Optional[Union[float, int]],
    band_pct: float = 0.10,
) -> bool:
    """Return True when a (market) order is safe against a reference price.

    A market order has no meaningful own price, so it is only acceptable when
    a recent reference quote exists; a priced (limit) order must additionally
    sit inside ``band_pct`` of the reference.  This is the ported CWE-20
    "no reference price => reject" guard.

    * ``reference_price`` missing/<= 0 -> False (reject).
    * ``price is None`` (true market order) -> True when a reference exists.
    * otherwise ``abs(price - reference)/reference <= band_pct``.
    """
    try:
        ref = float(reference_price)
    except (TypeError, ValueError):
        return False
    if ref <= 0:
        return False
    if price is None:
        return True
    try:
        px = float(price)
    except (TypeError, ValueError):
        return False
    if px <= 0:
        return False
    return abs(px - ref) / ref <= max(0.0, band_pct)
