"""Headless localhost JSON HTTP service wrapping the CybosPlus bridge.

Uses ``http.server.ThreadingHTTPServer`` bound to 127.0.0.1:8100 (overridable
via :class:`bridge.config.Config`).  Every response is JSON UTF-8 with the
envelope ``{ok: true, ...}`` or ``{ok: false, error: {code, message,
action?}}``.  Request bodies are validated; malformed JSON/type errors answer
HTTP 400, business errors answer HTTP 200 with ``ok: false``.  Handlers never
raise past ``BaseHTTPRequestHandler``.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .config import Config
from .orders import (
    cancel_order,
    default_session,
    fetch_balance,
    fetch_daily_pnl,
    fetch_open_orders,
    fetch_positions,
    fetch_quotes,
    place_order,
)
from .plus import BridgeError, CybosPlus
from .realtime import RealtimeHub
from .signals import fetch_rank

_UTF8 = "utf-8"

# route -> (required_method, handler_suffix)
_GET_ROUTES = frozenset(
    {"/health", "/balance", "/positions", "/orders", "/quotes", "/rank"}
)
_POST_ROUTES = frozenset({"/order", "/cancel", "/subscribe", "/unsubscribe", "/shutdown"})


class BridgeApp:
    """Composition root wiring the CybosPlus session, realtime hub and config."""

    def __init__(
        self,
        plus: Optional[CybosPlus] = None,
        hub: Optional[RealtimeHub] = None,
        config: Optional[Config] = None,
    ) -> None:
        self.plus = plus if plus is not None else default_session()
        self.hub = hub if hub is not None else RealtimeHub(self.plus)
        self.config = config if config is not None else Config.from_env()

    # ------------------------------------------------------------------ #
    def make_server(self) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer((self.config.host, self.config.port), _Handler)
        server.daemon_threads = True
        server.app = self  # type: ignore[attr-defined]
        return server


class _Handler(BaseHTTPRequestHandler):
    server_version = "creon-bridge/1.0"

    # -- helpers -------------------------------------------------------- #
    @property
    def app(self) -> BridgeApp:  # type: ignore[override]
        return self.server.app  # type: ignore[attr-defined, no-any-return]

    def log_message(self, fmt: str, *args) -> None:  # quiet the default logger
        return

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode(_UTF8)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            # The client hung up before reading the answer -- ConnectionAborted
            # (WinError 10053), BrokenPipe and ConnectionReset all land here.
            # The loop's first /health after a bridge restart times out and
            # aborts the socket, and the ThreadingHTTPServer would otherwise
            # dump a full traceback into bridge.log for a request that was
            # answered as far as it could be.  Not a server fault: drop it.
            return

    def _send_ok(self, data: dict) -> None:
        self._send(200, {"ok": True, **data})

    def _send_error(self, code: str, message: str, action: str = None, status: int = 200) -> None:
        error: Dict[str, str] = {"code": code, "message": message}
        if action:
            error["action"] = action
        self._send(status, {"ok": False, "error": error})

    def _send_exception(self, exc: Exception) -> None:
        if isinstance(exc, BridgeError):
            self._send(200, {"ok": False, "error": exc.as_dict()})
        else:
            self._send(200, {"ok": False, "error": {"code": "server_error",
                                                    "message": str(exc)}})

    def _read_json(self) -> Optional[dict]:
        """Parse a JSON request body; answer HTTP 400 on malformed input."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
        except OSError:
            return None
        try:
            data = json.loads(raw.decode(_UTF8))
        except (ValueError, UnicodeDecodeError):
            self._send_error("bad_request", "request body is not valid JSON", status=400)
            return None
        if not isinstance(data, dict):
            self._send_error("bad_request", "request body must be a JSON object", status=400)
            return None
        return data

    @staticmethod
    def _expect(data: dict, field: str) -> Tuple[bool, object]:
        if field not in data or data[field] is None:
            return False, field + " is required"
        return True, data[field]

    @staticmethod
    def _ts() -> float:
        return time.time()

    # -- routing -------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def _route(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if method == "GET" and path in _GET_ROUTES:
            handler = {
                "/health": self._get_health,
                "/balance": self._get_balance,
                "/positions": self._get_positions,
                "/orders": self._get_orders,
                "/quotes": self._get_quotes,
                "/rank": self._get_rank,
            }[path]
            handler(query=parse_qs(parsed.query))
            return
        if method == "POST" and path in _POST_ROUTES:
            handler = {
                "/order": self._post_order,
                "/cancel": self._post_cancel,
                "/subscribe": self._post_subscribe,
                "/unsubscribe": self._post_unsubscribe,
                "/shutdown": self._post_shutdown,
            }[path]
            handler()
            return
        self._send_error(
            "not_found", "no such endpoint: {0} {1}".format(method, path),
            status=404,
        )

    # -- GET handlers --------------------------------------------------- #
    def _get_health(self, query: Dict[str, list] = None) -> None:
        connected = self.app.plus.is_connected()
        self._send_ok({"connected": connected, "ts": self._ts()})

    def _get_balance(self, query: Dict[str, list] = None) -> None:
        try:
            balance = fetch_balance(self.app.plus)
        except Exception as exc:  # noqa: BLE001 - never raise past the handler
            self._send_exception(exc)
            return
        self._send_ok({"balance": balance, "ts": self._ts()})

    def _get_positions(self, query: Dict[str, list] = None) -> None:
        try:
            positions = fetch_positions(self.app.plus)
        except Exception as exc:  # noqa: BLE001
            self._send_exception(exc)
            return
        self._send_ok({"positions": positions, "ts": self._ts()})

    def _get_orders(self, query: Dict[str, list] = None) -> None:
        try:
            orders = fetch_open_orders(self.app.plus)
        except Exception as exc:  # noqa: BLE001
            self._send_exception(exc)
            return
        self._send_ok({"orders": orders, "ts": self._ts()})

    def _get_quotes(self, query: Dict[str, list] = None) -> None:
        query = query or {}
        codes_raw = query.get("codes")
        if codes_raw:
            codes = [c.strip() for c in codes_raw[0].split(",") if c.strip()]
            try:
                quotes = fetch_quotes(self.app.plus, codes)
            except Exception as exc:  # noqa: BLE001
                self._send_exception(exc)
                return
            self._send_ok({"quotes": quotes, "ts": self._ts()})
            return
        # no codes requested -> return whatever the realtime hub has cached
        self._send_ok({"quotes": self.app.hub.latest_quotes(), "ts": self._ts()})

    def _get_rank(self, query: Dict[str, list] = None) -> None:
        """Ranking list (당일 상승률/하락률 상위) -- see :mod:`bridge.signals`.

        Query parameters (all optional):
        ``limit`` (default 60, capped at 200), ``direction`` (up|down),
        ``pct_from`` / ``pct_to`` (등락률 window, default 0..30 like the
        sample -- pass a wider ``pct_to`` to include 상한가), ``sort``,
        ``market``, ``ord_style`` (char|int, the sample's ord() question) and
        ``raw`` (a JSON object of index->value overriding everything, for
        one-off live A/B tests).
        """
        try:
            params = _rank_params(query or {})
            result = fetch_rank(self.app.plus, **params)
        except Exception as exc:  # noqa: BLE001 - never raise past the handler
            self._send_exception(exc)
            return
        self._send_ok(
            {
                "rank": result["items"],
                "total_count": result["total_count"],
                "pages": result["pages"],
                "ts": self._ts(),
            }
        )

    # -- POST handlers -------------------------------------------------- #
    def _post_order(self) -> None:
        data = self._read_json()
        if data is None:
            return
        side = data.get("side")
        code = data.get("code")
        qty = data.get("qty")
        price = data.get("price")
        order_type = data.get("order_type", "limit")
        if not isinstance(side, str) or not isinstance(code, str):
            self._send_error("invalid_input", "side and code are required")
            return
        try:
            order = place_order(
                self.app.plus, side=side, code=code, qty=qty,
                price=price, order_type=order_type,
            )
        except Exception as exc:  # noqa: BLE001
            self._send_exception(exc)
            return
        self._send_ok({"order": order, "ts": self._ts()})

    def _post_cancel(self) -> None:
        data = self._read_json()
        if data is None:
            return
        order_no = data.get("order_no")
        code = data.get("code")
        qty = data.get("qty")
        if order_no is None or not isinstance(code, str):
            self._send_error("invalid_input", "order_no and code are required")
            return
        try:
            cancel = cancel_order(self.app.plus, order_no=order_no, code=code, qty=qty)
        except Exception as exc:  # noqa: BLE001
            self._send_exception(exc)
            return
        self._send_ok({"cancel": cancel, "ts": self._ts()})

    def _post_subscribe(self) -> None:
        data = self._read_json()
        if data is None:
            return
        codes = data.get("codes")
        if not isinstance(codes, list) or not codes:
            self._send_error("invalid_input", "codes must be a non-empty list")
            return
        try:
            self.app.hub.subscribe([c for c in codes if isinstance(c, str)])
        except Exception as exc:  # noqa: BLE001
            self._send_exception(exc)
            return
        self._send_ok({"subscribed": codes, "ts": self._ts()})

    def _post_unsubscribe(self) -> None:
        data = self._read_json()
        if data is None:
            return
        codes = data.get("codes")
        if not isinstance(codes, list) or not codes:
            self._send_error("invalid_input", "codes must be a non-empty list")
            return
        try:
            self.app.hub.unsubscribe([c for c in codes if isinstance(c, str)])
        except Exception as exc:  # noqa: BLE001
            self._send_exception(exc)
            return
        self._send_ok({"unsubscribed": codes, "ts": self._ts()})

    def _post_shutdown(self) -> None:
        # server.shutdown() must not run from a request-handler thread, so
        # schedule it on a short-lived background thread.
        def _stop() -> None:
            time.sleep(0.05)
            self.server.shutdown()  # type: ignore[attr-defined]

        threading.Thread(target=_stop, daemon=True).start()
        self._send_ok({"shutdown": True, "ts": self._ts()})


def _rank_params(query: Dict[str, list]) -> Dict[str, object]:
    """Parse + validate the ``/rank`` query string into ``fetch_rank`` kwargs.

    :raises BridgeError: on any malformed value (surfaced as ``invalid_input``).
    """

    def one(name: str, default):
        values = query.get(name)
        if not values:
            return default
        return values[0]

    def as_int(name: str, default: int) -> int:
        raw = one(name, None)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise BridgeError("invalid_input", "{0} must be an integer".format(name))

    raw_inputs = None
    raw_json = one("raw", None)
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except ValueError:
            raise BridgeError("invalid_input", "raw must be a JSON object")
        if not isinstance(parsed, dict):
            raise BridgeError("invalid_input", "raw must be a JSON object")
        raw_inputs = parsed

    limit = max(1, min(200, as_int("limit", 60)))
    return {
        "direction": one("direction", "up"),
        "period": one("period", "day"),
        "market": one("market", "0"),
        "sort": as_int("sort", 21),
        "exclude_managed": str(one("exclude_managed", "1")).lower()
        not in ("0", "false", "no"),
        "volume_filter": one("volume_filter", "0"),
        "display": one("display", "0"),
        "pct_from": as_int("pct_from", 0),
        "pct_to": as_int("pct_to", 30),
        "limit": limit,
        "ord_style": one("ord_style", "char"),
        "raw_inputs": raw_inputs,
    }


def create_server(app: Optional[BridgeApp] = None) -> ThreadingHTTPServer:
    """Convenience factory used by ``run_bridge.py`` and tests."""
    if app is None:
        app = BridgeApp()
    return app.make_server()


class ServeContext:
    """Context manager binding a server to a live port for tests."""

    def __init__(self, app: Optional[BridgeApp] = None) -> None:
        self.app = app if app is not None else BridgeApp()
        self.server = self.app.make_server()
        self.port = self.server.server_address[1]
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "ServeContext":
        self._thread = threading.Thread(
            target=self.server.serve_forever, name="bridge-test-server", daemon=True
        )
        self._thread.start()
        return self

    def url(self, path: str = "") -> str:
        return "http://127.0.0.1:{0}{1}".format(self.port, path)

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
