"""HTTP client for the creon-bridge localhost API.

Response-key contract derived from ``bridge/server.py`` + ``bridge/orders.py``
(READ-ONLY references in this repo).  Every bridge response is the envelope::

    {ok: true, ...}  or  {ok: false, error: {code, message, action?}}

Endpoints (all ``ts`` timestamps are ignored by this client):

* GET  /health            -> ``{connected: bool}``
* GET  /balance           -> ``{balance: {account, goods_flag,
        positions_count, total_buy_amount, total_eval_amount,
        total_eval_pnl}}``
* GET  /positions         -> ``{positions: [{code, name, credit_type,
        loan_date, qty, sellable_qty, avg_price, eval_amount, eval_pnl}]}``
* GET  /orders            -> ``{orders: [{order_no, original_order_no, code,
        name, description, qty, price, filled_qty, credit, cancelable_qty,
        bs_code, loan_date, order_hoga_desc, order_hoga_code}]}``
* GET  /quotes?codes=a,b  -> ``{quotes: {CODE: {code, name, time, current,
        diff, open, high, low, offer1, bid1, volume, amount, base}}}``
* POST /order             body ``{side, code, qty, price?, order_type?}``
                          -> ``{order: {order_no, side, code, qty, price,
                          order_type, message}}``
* POST /cancel            body ``{order_no, code, qty?}``
                          -> ``{cancel: {order_no, code, qty, message}}``
* POST /subscribe         body ``{codes: [...]}`` -> ``{subscribed: [...]}``
* POST /unsubscribe       body ``{codes: [...]}`` -> ``{unsubscribed: [...]}``
* POST /shutdown          -> ``{shutdown: true}``

Codes sent to the bridge are always plain 6 digits (never ``A``-prefixed).
Every request runs with a timeout and returns the typed payload (or raises a
typed :class:`BridgeError`).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import net
from .feed import normalize_code


class BridgeError(Exception):
    """Typed bridge failure carrying code/message/action."""

    def __init__(
        self,
        code: str,
        message: str,
        action: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action

    def as_dict(self) -> Dict[str, str]:
        result = {"code": self.code, "message": self.message}
        if self.action:
            result["action"] = self.action
        return result

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        if self.action:
            return "{0} [{1}] ({2})".format(self.message, self.code, self.action)
        return "{0} [{1}]".format(self.message, self.code)


class BridgeResponseError(BridgeError):
    """The bridge answered ``{ok: false, error: {...}}`` (business failure)."""


class BridgeTransportError(BridgeError):
    """No usable HTTP response (connection refused, timeout, HTTP error)."""


class BridgeClient:
    """Thin typed client over the bridge HTTP contract."""

    def __init__(self, base_url: str = "http://127.0.0.1:8100",
                 timeout: float = 5.0) -> None:
        """Construct a client for *base_url* (default bridge endpoint)."""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # GET endpoints
    # ------------------------------------------------------------------ #
    def health(self) -> Dict[str, Any]:
        """Return ``{connected: bool}`` from GET /health."""
        return self._get("/health")

    def balance(self) -> Dict[str, Any]:
        """Return the ``balance`` object from GET /balance."""
        return self._get("/balance").get("balance", {})

    def positions(self) -> List[Dict[str, Any]]:
        """Return the ``positions`` list from GET /positions."""
        return list(self._get("/positions").get("positions", []))

    def open_orders(self) -> List[Dict[str, Any]]:
        """Return the outstanding (unfilled) ``orders`` list from GET /orders."""
        return list(self._get("/orders").get("orders", []))

    def quotes(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """Return ``{CODE: {quote...}}`` from GET /quotes?codes=...

        ``codes`` are normalised to plain 6-digit stock codes.
        """
        plain = self._plain_codes(codes)
        if not plain:
            raise BridgeError("invalid_input", "quotes requires at least one code")
        payload = self._get("/quotes", {"codes": ",".join(plain)})
        return dict(payload.get("quotes", {}))

    def cached_quotes(self) -> Dict[str, Dict[str, Any]]:
        """Return the bridge's *realtime cache* (GET /quotes with no codes).

        This is the StockCur tick snapshot the bridge already holds, so it costs
        **no Daishin request** -- the sub-cycle exit watch can poll it every
        second without touching the broker budget.  Every quote carries a ``ts``
        (epoch seconds) which callers must use to reject stale ticks.
        """
        return dict(self._get("/quotes").get("quotes", {}))

    def rank(
        self,
        limit: int = 60,
        direction: str = "up",
        pct_from: int = 0,
        pct_to: int = 30,
        sort: int = 21,
        market: str = "0",
        ord_style: str = "char",
    ) -> Dict[str, Any]:
        """Return the 당일 상승률/하락률 ranking from GET /rank.

        Shape: ``{"items": [{code, name, diff_sign, diff, volume, rank}...],
        "total_count": int, "pages": int}``.  Each page of the broker-side
        ranking costs a quote request, so ``limit`` is a real cost knob.
        """
        payload = self._get(
            "/rank",
            {
                "limit": str(max(1, min(200, int(limit)))),
                "direction": direction,
                "pct_from": str(int(pct_from)),
                "pct_to": str(int(pct_to)),
                "sort": str(int(sort)),
                "market": str(market),
                "ord_style": ord_style,
            },
        )
        return {
            "items": list(payload.get("rank", [])),
            "total_count": payload.get("total_count"),
            "pages": payload.get("pages"),
        }

    # ------------------------------------------------------------------ #
    # POST endpoints
    # ------------------------------------------------------------------ #
    def order(
        self,
        side: str,
        code: str,
        qty: int,
        price: Optional[int] = None,
        order_type: str = "limit",
    ) -> Dict[str, Any]:
        """Place an order; returns the bridge ``order`` result object."""
        plain = self._plain_code(code)
        body: Dict[str, Any] = {
            "side": side,
            "code": plain,
            "qty": int(qty),
            "order_type": order_type,
        }
        if price is not None:
            body["price"] = int(price)
        return self._post("/order", body).get("order", {})

    def cancel(
        self, order_no: str, code: str, qty: Optional[int] = None
    ) -> Dict[str, Any]:
        """Cancel an outstanding order; returns the ``cancel`` object."""
        plain = self._plain_code(code)
        body: Dict[str, Any] = {"order_no": str(order_no), "code": plain}
        if qty is not None:
            body["qty"] = int(qty)
        return self._post("/cancel", body).get("cancel", {})

    def subscribe(self, codes: List[str]) -> List[str]:
        """Subscribe to realtime quotes; returns the acknowledged codes."""
        plain = self._plain_codes(codes)
        if not plain:
            raise BridgeError("invalid_input", "subscribe requires codes")
        return list(self._post("/subscribe", {"codes": plain}).get("subscribed", []))

    def unsubscribe(self, codes: List[str]) -> List[str]:
        """Unsubscribe from realtime quotes; returns the acknowledged codes."""
        plain = self._plain_codes(codes)
        if not plain:
            raise BridgeError("invalid_input", "unsubscribe requires codes")
        return list(self._post("/unsubscribe", {"codes": plain}).get("unsubscribed", []))

    def shutdown(self) -> bool:
        """Ask the bridge to shut down; returns True when acknowledged."""
        return bool(self._post("/shutdown", {}).get("shutdown", False))

    # ------------------------------------------------------------------ #
    # Transport helpers
    # ------------------------------------------------------------------ #
    def _url(self, path: str, query: Optional[Dict[str, str]] = None) -> str:
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def _get(
        self, path: str, query: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        request = urllib.request.Request(
            self._url(path, query), method="GET", headers={"Accept": "application/json"}
        )
        return self._roundtrip(request)

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._url(path),
            data=data,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        return self._roundtrip(request)

    def _roundtrip(self, request: urllib.request.Request) -> Dict[str, Any]:
        try:
            with net.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = int(getattr(resp, "status", 200))
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            try:
                raw = exc.read().decode("utf-8", errors="replace")
            except OSError:
                raw = ""
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None) or exc
            raise BridgeTransportError(
                "connection_failed", "bridge unreachable: {0}".format(reason)
            )
        except OSError as exc:
            raise BridgeTransportError(
                "connection_failed", "bridge request failed: {0}".format(exc)
            )
        return self._decode(raw, status)

    @staticmethod
    def _decode(raw: str, status: int) -> Dict[str, Any]:
        try:
            payload = json.loads(raw)
        except ValueError:
            raise BridgeTransportError(
                "bad_response", "bridge returned non-JSON body (HTTP {0})".format(status)
            )
        if not isinstance(payload, dict):
            raise BridgeTransportError(
                "bad_response", "bridge returned a non-object payload"
            )
        ok = payload.get("ok")
        if ok is False:
            error = payload.get("error")
            if isinstance(error, dict):
                code = str(error.get("code", "bridge_error"))
                message = str(error.get("message", "bridge error"))
                action = error.get("action")
                raise BridgeResponseError(
                    code,
                    message,
                    str(action) if action else None,
                )
            raise BridgeResponseError("bridge_error", "bridge answered ok:false")
        if ok is not True:
            # A 2xx without an explicit ok flag is not part of the contract.
            raise BridgeTransportError(
                "bad_response",
                "bridge payload missing ok flag (HTTP {0})".format(status),
            )
        return payload

    # ------------------------------------------------------------------ #
    # Code helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _plain_code(code: str) -> str:
        plain = normalize_code(code)
        if plain is None:
            raise BridgeError(
                "invalid_input", "code must be a 6 digit stock code: {0!r}".format(code)
            )
        return plain

    @classmethod
    def _plain_codes(cls, codes: List[str]) -> List[str]:
        plain: List[str] = []
        for code in codes:
            normalised = normalize_code(code)
            if normalised is not None:
                plain.append(normalised)
        return plain
