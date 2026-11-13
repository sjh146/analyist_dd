"""In-process deterministic mock bridge (paper fills).

Mirrors the ``mock_creon_executor.py`` pattern (README reference asset): every
order fills instantly at a deterministic price.  ``MockBridge`` exposes the
same duck-typed surface as :class:`trader_core.bridge_client.BridgeClient`
(``health/balance/positions/open_orders/quotes/cached_quotes/order/cancel/
subscribe/unsubscribe/shutdown``) so ``TraderEngine`` and ``PositionMonitor``
can run against it without any network, and it keeps a small internal ledger so
engine-level cash/position limits behave realistically.

Fill rules (documented):

* limit orders fill at the given limit ``price``;
* market orders fill at the current internal last price for the code
  (seeded via :meth:`set_quote` / :meth:`set_quotes`); with no reference
  price a market order is rejected (CWE-20 style);
* a buy that exceeds cash is rejected; a sell that exceeds the holding is
  rejected; otherwise the full quantity fills and the ledger updates.
"""

from __future__ import annotations

import itertools
import time
from typing import Any, Dict, List, Optional

from .bridge_client import BridgeResponseError
from .feed import normalize_code


def _num(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class MockBridge:
    """Stateful paper bridge: instant fills with a simple cash ledger."""

    def __init__(
        self,
        start_cash: float = 10_000_000.0,
        positions: Optional[Dict[str, Dict[str, Any]]] = None,
        quotes: Optional[Dict[str, float]] = None,
    ) -> None:
        self.cash = float(start_cash)
        # code -> {"qty": int, "avg_price": float}
        self._positions: Dict[str, Dict[str, Any]] = {}
        for code, pos in (positions or {}).items():
            plain = self._code(code)
            self._positions[plain] = {
                "qty": _num(pos.get("qty", 0)),
                "avg_price": float(pos.get("avg_price", pos.get("price", 0)) or 0),
            }
        # code -> last trade price
        self._quotes: Dict[str, float] = {}
        if quotes:
            self.set_quotes(quotes)
        # code -> realtime tick (what /quotes with no codes returns)
        self._ticks: Dict[str, Dict[str, Any]] = {}
        self._open_orders: Dict[str, Dict[str, Any]] = {}
        self._order_seq = itertools.count(1)
        self._shutdown = False

    # ------------------------------------------------------------------ #
    # Seeding helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _code(code: str) -> str:
        plain = normalize_code(code)
        if plain is None:
            raise BridgeResponseError(
                "invalid_input", "code must be a 6 digit stock code: {0!r}".format(code)
            )
        return plain

    def set_quote(self, code: str, price: float) -> None:
        """Seed/replace the internal last price for *code*."""
        self._quotes[self._code(code)] = float(price)

    def set_quotes(self, quotes: Dict[str, float]) -> None:
        """Seed several last prices at once (code -> price)."""
        for code, price in quotes.items():
            self.set_quote(code, price)

    def reset(self) -> None:
        """Reset the ledger to the constructor defaults."""
        self.cash = 10_000_000.0
        self._positions = {}
        self._quotes = {}
        self._ticks = {}
        self._open_orders = {}
        self._shutdown = False

    # ------------------------------------------------------------------ #
    # Bridge surface
    # ------------------------------------------------------------------ #
    def health(self) -> Dict[str, Any]:
        """Return the /health payload shape."""
        return {"connected": True}

    def balance(self) -> Dict[str, Any]:
        """Return the /balance payload shape (plus ``cash``/``equity``)."""
        total_eval = 0.0
        total_buy = 0.0
        total_pnl = 0.0
        for code, pos in self._positions.items():
            qty = _num(pos["qty"])
            avg = float(pos["avg_price"])
            last = self._quotes.get(code, avg)
            total_eval += qty * last
            total_buy += qty * avg
            total_pnl += qty * (last - avg)
        return {
            "account": "MOCK-ACCOUNT",
            "goods_flag": "01",
            "positions_count": len(self._positions),
            "total_buy_amount": total_buy,
            "total_eval_amount": total_eval,
            "total_eval_pnl": total_pnl,
            "cash": self.cash,
            "equity": self.cash + total_eval,
        }

    def positions(self) -> List[Dict[str, Any]]:
        """Return the /positions payload shape (plain 6-digit codes)."""
        result: List[Dict[str, Any]] = []
        for code, pos in self._positions.items():
            qty = _num(pos["qty"])
            if qty <= 0:
                continue
            avg = float(pos["avg_price"])
            last = self._quotes.get(code, avg)
            result.append(
                {
                    "code": code,
                    "name": "",
                    "credit_type": "",
                    "loan_date": "",
                    "qty": qty,
                    "sellable_qty": qty,
                    "avg_price": avg,
                    "eval_amount": qty * last,
                    "eval_pnl": qty * (last - avg),
                }
            )
        return result

    def open_orders(self) -> List[Dict[str, Any]]:
        """Return the /orders payload shape (normally empty; instant fills)."""
        return list(self._open_orders.values())

    def quotes(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """Return a /quotes payload snapshot for the requested codes."""
        result: Dict[str, Dict[str, Any]] = {}
        for code in codes:
            plain = self._code(code)
            if plain not in self._quotes:
                continue
            current = self._quotes[plain]
            result[plain] = {
                "code": plain,
                "name": "",
                "time": "",
                "current": current,
                "diff": 0,
                "open": 0,
                "high": 0,
                "low": 0,
                "offer1": current,
                "bid1": current,
                "volume": 0,
                "amount": 0,
                "base": 0,
            }
        return result

    def set_tick(self, code: str, price: float, ts: Optional[float] = None) -> None:
        """Seed the *realtime cache* for *code* (what ``cached_quotes`` returns).

        ``ts`` defaults to now; pass an old epoch value to emulate a stalled
        realtime feed.
        """
        plain = self._code(code)
        self._ticks[plain] = {
            "code": plain,
            "name": "",
            "current": float(price),
            "ts": time.time() if ts is None else float(ts),
            "time": "",
        }

    def cached_quotes(self) -> Dict[str, Dict[str, Any]]:
        """Return the seeded realtime cache (no broker request in the mock)."""
        return {code: dict(tick) for code, tick in self._ticks.items()}

    def order(
        self,
        side: str,
        code: str,
        qty: int,
        price: Optional[int] = None,
        order_type: str = "limit",
    ) -> Dict[str, Any]:
        """Fill an order instantly and update the internal ledger."""
        plain = self._code(code)
        qty_i = _num(qty)
        if qty_i <= 0:
            raise BridgeResponseError(
                "invalid_input", "qty must be a positive integer"
            )
        side_k = str(side).lower()
        if side_k not in ("buy", "sell"):
            raise BridgeResponseError(
                "invalid_input", "side must be 'buy' or 'sell'"
            )
        type_k = str(order_type).lower()
        if type_k not in ("limit", "market"):
            raise BridgeResponseError(
                "invalid_input", "order_type must be 'limit' or 'market'"
            )
        if price is not None and int(price) > 0 and type_k == "market":
            fill_price = float(int(price))
        elif price is not None and int(price) > 0:
            fill_price = float(int(price))
        elif plain in self._quotes:
            fill_price = float(self._quotes[plain])
        else:
            raise BridgeResponseError(
                "no_reference_price",
                "market order for {0} has no reference price".format(plain),
                "seed a quote before placing a market order",
            )
        order_no = "MN-{0}".format(next(self._order_seq))
        if side_k == "buy":
            amount = fill_price * qty_i
            if amount > self.cash:
                raise BridgeResponseError(
                    "insufficient_funds",
                    "buy of {0} KRW exceeds cash {1}".format(amount, self.cash),
                )
            pos = self._positions.setdefault(
                plain, {"qty": 0, "avg_price": 0.0}
            )
            old_qty = _num(pos["qty"])
            new_qty = old_qty + qty_i
            pos["avg_price"] = (
                (pos["avg_price"] * old_qty + amount) / new_qty if new_qty else 0.0
            )
            pos["qty"] = new_qty
            self.cash -= amount
        else:
            pos = self._positions.get(plain)
            held = _num(pos["qty"]) if pos else 0
            if held < qty_i:
                raise BridgeResponseError(
                    "insufficient_position",
                    "sell of {0} exceeds held {1}".format(qty_i, held),
                )
            remaining = held - qty_i
            if remaining > 0:
                self._positions[plain]["qty"] = remaining
            else:
                self._positions.pop(plain, None)
            self.cash += fill_price * qty_i
        self._quotes[plain] = fill_price
        return {
            "order_no": order_no,
            "side": side_k,
            "code": plain,
            "qty": qty_i,
            "price": int(fill_price),
            "order_type": type_k,
            "message": "mock filled",
            "status": "filled",
        }

    def cancel(
        self, order_no: str, code: str, qty: Optional[int] = None
    ) -> Dict[str, Any]:
        """Cancel an outstanding order (instant-fill mock keeps none open)."""
        plain = self._code(code)
        if str(order_no) not in self._open_orders:
            raise BridgeResponseError(
                "unknown_order", "no outstanding order {0}".format(order_no)
            )
        cancelled = self._open_orders.pop(str(order_no))
        return {
            "order_no": str(order_no),
            "code": plain,
            "qty": _num(cancelled.get("qty", qty or 0)),
            "message": "mock cancelled",
        }

    def subscribe(self, codes: List[str]) -> List[str]:
        """Acknowledge a realtime subscribe (no-op in the mock)."""
        return [self._code(code) for code in codes]

    def unsubscribe(self, codes: List[str]) -> List[str]:
        """Acknowledge a realtime unsubscribe (no-op in the mock)."""
        return [self._code(code) for code in codes]

    def shutdown(self) -> bool:
        """Flag shutdown and return True."""
        self._shutdown = True
        return True
