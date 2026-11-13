"""End-to-end HTTP tests: ThreadingHTTPServer + fake CybosPlus.

Exercises /health, /order (happy + error), /positions, body validation and
the JSON error envelope without any pywin32.
"""

from __future__ import annotations

import http.client
import json
import unittest

from bridge.config import Config
from bridge.plus import CybosPlus
from bridge.realtime import RealtimeHub
from bridge.server import BridgeApp, ServeContext
from bridge.tests import support

ACCOUNT = support.ACC


class BridgeServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.fakes = support.fake_session()
        app = BridgeApp(
            plus=self.session,
            hub=RealtimeHub(self.session),
            config=Config(host="127.0.0.1", port=0),
        )
        self._ctx = ServeContext(app)
        self._ctx.__enter__()
        self.port = self._ctx.port

    def tearDown(self) -> None:
        self._ctx.__exit__(None, None, None)

    def _request(self, method: str, path: str, body=None) -> tuple:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            payload = None
            headers = {}
            if body is not None:
                if isinstance(body, (dict, list)):
                    payload = json.dumps(body).encode("utf-8")
                else:
                    payload = body.encode("utf-8") if isinstance(body, str) else body
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except ValueError:
                data = raw
            return resp.status, data
        finally:
            conn.close()

    def test_health(self) -> None:
        status, data = self._request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["connected"])

    def test_order_happy_path(self) -> None:
        status, data = self._request(
            "POST", "/order",
            {"side": "buy", "code": "005930", "qty": 10, "price": 14100,
             "order_type": "limit"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        order = data["order"]
        self.assertEqual(order["code"], "005930")
        self.assertEqual(order["price"], 14100)
        order_com = self.fakes["order"]
        self.assertEqual(order_com.inputs[3], "A005930")
        self.assertEqual(order_com.inputs[8], "01")

    def test_order_error_path(self) -> None:
        self.fakes["order"].dib_status = 1
        self.fakes["order"].dib_msg = "rejected by broker"
        status, data = self._request(
            "POST", "/order",
            {"side": "buy", "code": "005930", "qty": 10, "price": 14100,
             "order_type": "limit"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"]["code"], "remote_error")
        self.assertIn("action", data["error"])

    def test_order_invalid_side(self) -> None:
        status, data = self._request(
            "POST", "/order", {"side": "sideways", "code": "005930", "qty": 1}
        )
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"]["code"], "invalid_input")

    def test_order_malformed_body_returns_400(self) -> None:
        status, data = self._request("POST", "/order", "{not json")
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"]["code"], "bad_request")

    def test_unknown_route_returns_404(self) -> None:
        status, data = self._request("GET", "/nope")
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])

    def test_positions_endpoint(self) -> None:
        obj = support.client().make("CpTrade.CpTd6033")
        obj.headers[7] = 1
        obj.rows[0] = ["Samsung"]
        obj.rows[1] = [" "]
        obj.rows[7] = [100]
        obj.rows[9] = [1600000]
        obj.rows[11] = [100000]
        obj.rows[12] = ["A005930"]
        obj.rows[15] = [100]
        obj.rows[17] = [15000]
        obj._continue = False
        status, data = self._request("GET", "/positions")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["positions"][0]["code"], "005930")

    def test_quotes_endpoint_with_codes(self) -> None:
        obj = support.client().make("DsCbo1.StockMst")
        obj.headers[0] = "A005930"
        obj.headers[1] = "Samsung"
        obj.headers[11] = 60000
        obj.headers[18] = 12345
        status, data = self._request("GET", "/quotes?codes=005930")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["quotes"]["005930"]["current"], 60000)

    def test_client_abort_mid_response_is_not_a_server_error(self) -> None:
        """WinError 10053 during the response write must not raise or log.

        2026-09-21 (live): the loop's first ``/health`` after a bridge restart
        timed out client-side and aborted the socket; ``_send`` then failed and
        ThreadingHTTPServer printed a full traceback into bridge.log for a
        request that was answered as far as it could be.
        """
        from bridge.server import _Handler  # noqa: PLC0415

        for exc in (
            ConnectionAbortedError("[WinError 10053] aborted"),
            BrokenPipeError("broken pipe"),
            ConnectionResetError("reset"),
        ):
            handler = _Handler.__new__(_Handler)  # skip the socket constructor
            handler.send_response = lambda *a, **k: None
            handler.send_header = lambda *a, **k: None
            handler.end_headers = lambda: None

            class _Writer:
                def write(self, data, exc=exc):
                    raise exc

            handler.wfile = _Writer()  # type: ignore[assignment]
            self.assertIsNone(handler._send(200, {"ok": True}))

    def test_send_still_works_for_a_healthy_client(self) -> None:
        status, data = self._request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])


if __name__ == "__main__":
    unittest.main()
