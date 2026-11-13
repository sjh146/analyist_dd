"""Bridge HTTP client tests against an in-process fake bridge server."""

from __future__ import annotations

import unittest

from trader_core import (
    BridgeClient,
    BridgeError,
    BridgeResponseError,
    BridgeTransportError,
)

from trader_core.tests import support


def _registry() -> dict:
    return {
        ("GET", "/health"): support.health_ok(connected=True),
        ("GET", "/balance"): support.balance_ok(
            positions_count=1, total_eval_amount=1_000_000
        ),
        ("GET", "/positions"): support.positions_ok([
            {"code": "005930", "name": "Samsung", "qty": 10, "avg_price": 10000,
             "sellable_qty": 10, "eval_amount": 110000},
        ]),
        ("GET", "/orders"): support.orders_ok([
            {"order_no": 123, "code": "005930", "qty": 10, "price": 10000,
             "filled_qty": 0, "cancelable_qty": 10},
        ]),
        ("GET", "/quotes"): support.quotes_ok({
            "005930": {"code": "005930", "current": 60000, "time": "101500"},
        }),
        ("POST", "/order"): support.order_ok(),
        ("POST", "/cancel"): support.cancel_ok(),
        ("POST", "/subscribe"): {"ok": True, "subscribed": ["005930"], "ts": 1.0},
        ("POST", "/unsubscribe"): {"ok": True, "unsubscribed": ["005930"], "ts": 1.0},
        ("POST", "/shutdown"): {"ok": True, "shutdown": True, "ts": 1.0},
    }


class BridgeClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = support.FakeBridge(_registry())
        self.fake.__enter__()
        self.client = BridgeClient(self.fake.base_url, timeout=2.0)

    def tearDown(self) -> None:
        self.fake.__exit__(None, None, None)

    def test_health(self) -> None:
        data = self.client.health()
        self.assertTrue(data["connected"])

    def test_balance(self) -> None:
        balance = self.client.balance()
        self.assertEqual(balance["positions_count"], 1)
        self.assertEqual(balance["total_eval_amount"], 1_000_000)

    def test_positions(self) -> None:
        rows = self.client.positions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "005930")

    def test_open_orders(self) -> None:
        rows = self.client.open_orders()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["qty"], 10)

    def test_quotes(self) -> None:
        quotes = self.client.quotes(["005930"])
        self.assertEqual(quotes["005930"]["current"], 60000)
        # A-prefixed codes are normalised to plain before the request
        self.client.quotes(["A000660", "005930"])

    def test_order_happy(self) -> None:
        order = self.client.order(side="buy", code="005930", qty=10,
                                  price=10000, order_type="limit")
        self.assertEqual(order["code"], "005930")
        self.assertEqual(order["price"], 10000)

    def test_cancel(self) -> None:
        cancel = self.client.cancel("12345", "005930")
        self.assertEqual(cancel["code"], "005930")

    def test_subscribe_unsubscribe_shutdown(self) -> None:
        self.assertEqual(self.client.subscribe(["005930"]), ["005930"])
        self.assertEqual(self.client.unsubscribe(["005930"]), ["005930"])
        self.assertTrue(self.client.shutdown())

    def test_invalid_code_rejected_client_side(self) -> None:
        with self.assertRaises(BridgeError) as cm:
            self.client.order(side="buy", code="not-a-code", qty=1, price=100)
        self.assertEqual(cm.exception.code, "invalid_input")

    def test_error_envelope_raises_typed(self) -> None:
        with support.FakeBridge({
            ("POST", "/order"): (
                200,
                support.error_envelope("remote_error", "rejected by broker",
                                       "check_order/account"),
            ),
        }) as failing:
            client = BridgeClient(failing.base_url, timeout=2.0)
            with self.assertRaises(BridgeResponseError) as cm:
                client.order(side="buy", code="005930", qty=1, price=100)
            self.assertEqual(cm.exception.code, "remote_error")
            self.assertEqual(cm.exception.action, "check_order/account")

    def test_http_400_raises_typed(self) -> None:
        with support.FakeBridge({
            ("POST", "/order"): (
                400,
                support.error_envelope("bad_request", "request body is not valid JSON"),
            ),
        }) as failing:
            client = BridgeClient(failing.base_url, timeout=2.0)
            with self.assertRaises(BridgeResponseError) as cm:
                client.order(side="buy", code="005930", qty=1, price=100)
            self.assertEqual(cm.exception.code, "bad_request")

    def test_connection_refused_raises_transport(self) -> None:
        client = BridgeClient("http://127.0.0.1:1", timeout=0.5)
        with self.assertRaises(BridgeTransportError):
            client.health()


class BridgeClientIsolationTest(unittest.TestCase):
    """Verifies error routes without sharing registry state across tests."""

    def test_not_found_raises_typed(self) -> None:
        with support.FakeBridge({}) as empty:
            client = BridgeClient(empty.base_url, timeout=2.0)
            with self.assertRaises(BridgeResponseError) as cm:
                client.health()
            self.assertEqual(cm.exception.code, "not_found")

    def test_non_json_body_raises_transport(self) -> None:
        with support.FakeBridge({
            ("GET", "/health"): (200, "this is not a dict"),
        }) as odd:
            client = BridgeClient(odd.base_url, timeout=2.0)
            with self.assertRaises(BridgeTransportError):
                client.health()


if __name__ == "__main__":
    unittest.main()
