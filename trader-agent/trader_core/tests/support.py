"""Shared helpers for trader-core tests (fixtures, feed builders, fake HTTP)."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")
FEED_FIXTURE = os.path.join(FIXTURES_DIR, "screener_latest_sample.json")


def read_feed_fixture() -> str:
    """Return the real-shape screener fixture as a JSON string."""
    with open(FEED_FIXTURE, "r", encoding="utf-8") as handle:
        return handle.read()


def read_scoring_fixture() -> str:
    """Return the scoring_summary fixture as a JSON string."""
    with open(
        os.path.join(FIXTURES_DIR, "scoring_summary_sample.json"),
        "r",
        encoding="utf-8",
    ) as handle:
        return handle.read()


def make_candidate(
    rank: int = 1,
    code: str = "005930",
    name: str = "Sample",
    sector: str = "Tech",
    signal_date: str = "",
    close_price: str = "10000.0",
    score: str = "90.0",
    reason: str = "test signal",
    **extra: Any,
) -> Dict[str, Any]:
    """Build one feed candidate item with string-typed values.

    ``signal_date`` defaults to ``""`` (absent) so the candidate-level freshness
    gate (``runner.rules.stale_candidates``) does not decide the outcome of
    tests that are about other rules; freshness has its own cases in
    ``runner/tests/test_rules.py``.  Pass a date explicitly to exercise it.
    """
    item: Dict[str, Any] = {
        "rank": str(rank),
        "stock_code": code,
        "stock_name": name,
        "sector": sector,
        "signal_date": signal_date,
        "close_price": str(close_price),
        "score": str(score),
        "reason": reason,
    }
    for key, value in extra.items():
        item[key] = value if isinstance(value, str) else json.dumps(value)
    return item


def make_payload(
    generated_at: str = "2026-09-04T10:00:00+09:00",
    close: Optional[List[Dict[str, Any]]] = None,
    swing: Optional[List[Dict[str, Any]]] = None,
    daytrading: Optional[List[Dict[str, Any]]] = None,
    screener_stats: Optional[Dict[str, Any]] = None,
    include_scoring_summary: bool = True,
) -> Dict[str, Any]:
    """Build a full feed payload dict for tests."""
    candidates: Dict[str, Dict[str, Any]] = {}
    for name, items in (("close", close), ("swing", swing),
                        ("daytrading", daytrading)):
        item_list = items if items is not None else []
        if name == "close" and close is None and daytrading is None:
            item_list = [make_candidate()]
        candidates[name] = {
            "count": len(item_list),
            "file": "test_{0}.csv".format(name),
            "items": item_list,
        }
    payload: Dict[str, Any] = {
        "generated_at": generated_at,
        "source": "bc250_analyist_dd",
        "candidates": candidates,
    }
    if include_scoring_summary:
        payload["scoring_summary"] = {
            "generated_at": "2026-09-04T10:00:00",
            "total_scored": 0,
            "skipped_existing": 1,
            "screener_stats": screener_stats if screener_stats is not None else {},
        }
    return payload


# ---------------------------------------------------------------------- #
# Fake bridge HTTP server (responds with the contract-derived envelopes)
# ---------------------------------------------------------------------- #
class _FakeBridgeHandler(BaseHTTPRequestHandler):
    """Serves the response registry stored on ``self.server.registry``."""

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _answer(self, method: str) -> None:
        path = urlparse(self.path).path
        registry = getattr(self.server, "registry", {})
        entry = registry.get((method, path))
        if entry is None:
            status, payload = 404, {
                "ok": False,
                "error": {
                    "code": "not_found",
                    "message": "no such endpoint: {0} {1}".format(method, path),
                },
            }
        elif isinstance(entry, tuple) and len(entry) == 2:
            status, payload = entry
        else:  # a bare dict payload -> HTTP 200
            status, payload = 200, entry
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._answer("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._answer("POST")


class FakeBridge:
    """Context manager running a scripted bridge over a random local port.

    ``registry`` maps ``(method, path)`` to ``(http_status, payload_dict)``.
    """

    def __init__(
        self, registry: Dict[tuple, Any], port: int = 0
    ) -> None:
        self.registry = registry
        self.server = ThreadingHTTPServer(("127.0.0.1", port), _FakeBridgeHandler)
        self.server.registry = registry
        self.server.daemon_threads = True
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:{0}".format(self.server.server_address[1])

    def __enter__(self) -> "FakeBridge":
        self._thread = threading.Thread(
            target=self.server.serve_forever, name="fake-bridge", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)


# Contract-derived bridge response shapes (from bridge/server.py + orders.py).
def health_ok(connected: bool = True) -> Dict[str, Any]:
    return {"ok": True, "connected": connected, "ts": 1.0}


def balance_ok(**overrides: Any) -> Dict[str, Any]:
    payload = {
        "account": "1234567890",
        "goods_flag": "01",
        "positions_count": 0,
        "total_buy_amount": 0,
        "total_eval_amount": 0,
        "total_eval_pnl": 0,
    }
    payload.update(overrides)
    return {"ok": True, "balance": payload, "ts": 1.0}


def positions_ok(rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"ok": True, "positions": rows or [], "ts": 1.0}


def orders_ok(rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"ok": True, "orders": rows or [], "ts": 1.0}


def order_ok(**overrides: Any) -> Dict[str, Any]:
    payload = {
        "order_no": 12345,
        "side": "buy",
        "code": "005930",
        "qty": 10,
        "price": 10000,
        "order_type": "limit",
        "message": "ok",
    }
    payload.update(overrides)
    return {"ok": True, "order": payload, "ts": 1.0}


def cancel_ok(**overrides: Any) -> Dict[str, Any]:
    payload = {"order_no": "12345", "code": "005930", "qty": 0, "message": "ok"}
    payload.update(overrides)
    return {"ok": True, "cancel": payload, "ts": 1.0}


def quotes_ok(rows: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"ok": True, "quotes": rows or {}, "ts": 1.0}


def error_envelope(code: str = "remote_error", message: str = "boom",
                   action: Optional[str] = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if action:
        error["action"] = action
    return {"ok": False, "error": error}
