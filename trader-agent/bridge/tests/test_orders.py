"""Unit tests for order construction, cancellation and error mapping."""

from __future__ import annotations

import unittest

from bridge.orders import (
    cancel_order,
    fetch_balance,
    fetch_daily_pnl,
    fetch_open_orders,
    fetch_positions,
    fetch_quotes,
    place_order,
)
from bridge.plus import BridgeError
from bridge.tests import support


class OrderRequestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.fakes = support.fake_session()

    def test_buy_limit_request_shape(self) -> None:
        result = place_order(
            self.session, side="buy", code="005930", qty=10, price=14100,
        )
        order = self.fakes["order"]
        self.assertEqual(order.inputs[0], "2")          # buy
        self.assertEqual(order.inputs[1], support.ACC)  # account
        self.assertEqual(order.inputs[2], "01")         # goods type
        self.assertEqual(order.inputs[3], "A005930")    # A-prefixed trade code
        self.assertEqual(order.inputs[4], 10)           # qty
        self.assertEqual(order.inputs[5], 14100)        # price
        self.assertEqual(order.inputs[7], "0")          # order condition basic
        self.assertEqual(order.inputs[8], "01")         # quote regular/limit
        self.assertEqual(result["code"], "005930")
        self.assertEqual(result["side"], "buy")

    def test_sell_market_request_shape(self) -> None:
        place_order(
            self.session, side="sell", code="000660", qty=5, price=None,
            order_type="market",
        )
        order = self.fakes["order"]
        self.assertEqual(order.inputs[0], "1")          # sell
        self.assertEqual(order.inputs[3], "A000660")
        self.assertEqual(order.inputs[5], 0)            # market price is 0
        self.assertEqual(order.inputs[8], "03")         # quote market

    def test_cancel_request_shape_with_qty(self) -> None:
        cancel_order(
            self.session, order_no="777777", code="005930", qty=3,
        )
        cancel = self.fakes["cancel"]
        self.assertEqual(cancel.inputs[1], "777777")    # original order no
        self.assertEqual(cancel.inputs[2], support.ACC)
        self.assertEqual(cancel.inputs[3], "01")
        self.assertEqual(cancel.inputs[4], "A005930")
        self.assertEqual(cancel.inputs[5], 3)

    def test_cancel_without_qty_means_full_cancel(self) -> None:
        cancel_order(self.session, order_no="777777", code="005930", qty=None)
        self.assertEqual(self.fakes["cancel"].inputs[5], 0)  # remaining cancel

    def test_invalid_inputs_raise(self) -> None:
        with self.assertRaises(BridgeError) as cm:
            place_order(self.session, side="hold", code="005930", qty=1, price=100)
        self.assertEqual(cm.exception.code, "invalid_input")
        with self.assertRaises(BridgeError):
            place_order(self.session, side="buy", code="not-a-code", qty=1, price=100)
        with self.assertRaises(BridgeError):
            place_order(self.session, side="buy", code="005930", qty=0, price=100)
        with self.assertRaises(BridgeError):
            place_order(self.session, side="buy", code="005930", qty=1, price=0)

    def test_block_request_error_mapping(self) -> None:
        self.fakes["order"].block_ret = 1  # comms request failure
        with self.assertRaises(BridgeError) as cm:
            place_order(self.session, side="buy", code="005930", qty=1, price=100)
        self.assertEqual(cm.exception.code, "request_failed")

    def test_dib_status_error_mapping(self) -> None:
        self.fakes["order"].dib_status = 3
        self.fakes["order"].dib_msg = "order failure (cause code)"
        with self.assertRaises(BridgeError) as cm:
            place_order(self.session, side="buy", code="005930", qty=1, price=100)
        self.assertEqual(cm.exception.code, "remote_error")
        self.assertIn("3", cm.exception.message)

    def test_not_connected_error_mapping(self) -> None:
        self.fakes["cybos"].IsConnect = 0
        with self.assertRaises(BridgeError) as cm:
            place_order(self.session, side="buy", code="005930", qty=1, price=100)
        self.assertEqual(cm.exception.code, "not_connected")


class OutstandingOrdersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.fakes = support.fake_session()
        obj = self.fakes.setdefault("open_orders", support.client().make("CpTrade.CpTd5339"))
        obj.headers[5] = 2
        obj.rows[1] = ["1001", "1002"]
        obj.rows[2] = ["0", "0"]
        obj.rows[3] = ["A005930", "A000660"]
        obj.rows[4] = ["Samsung", "SK Hynix"]
        obj.rows[5] = ["sell", "buy"]
        obj.rows[6] = [10, 5]
        obj.rows[7] = [70000, 120000]
        obj.rows[8] = [0, 0]
        obj._continue = False

    def test_open_orders_parsing_and_code_strip(self) -> None:
        orders = fetch_open_orders(self.session)
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0]["order_no"], "1001")
        self.assertEqual(orders[0]["code"], "005930")  # A-prefix stripped
        self.assertEqual(orders[1]["code"], "000660")
        self.assertEqual(orders[0]["qty"], 10)


class RateLimitGateTest(unittest.TestCase):
    """Daishin request-budget gate (GetLimitRemainCount / LimitRequestRemainTime)."""

    def setUp(self) -> None:
        self.session, self.fakes = support.fake_session()

    def test_order_slot_free_passes(self) -> None:
        self.fakes["cybos"].limit_remain_count = 1  # one slot available
        # No exception: place_order flows through the gate (slot free).
        result = place_order(
            self.session, side="buy", code="005930", qty=10, price=14100,
        )
        self.assertEqual(result["code"], "005930")

    def test_exhausted_budget_raises_rate_limited(self) -> None:
        cybos = self.fakes["cybos"]
        cybos.limit_remain_count = 0
        cybos.LimitRequestRemainTime = 60000  # budget resets in 60s

        def gate() -> None:
            self.session.invoke(
                lambda p: p.wait_for_request_slot(timeout=0.05, min_sleep_ms=50)
            )

        with self.assertRaises(BridgeError) as cm:
            gate()
        self.assertEqual(cm.exception.code, "rate_limited")

    def test_gate_skips_without_counter_surface(self) -> None:
        # No GetLimitRemainCount support (old stub/broker client) => pass
        # through; the BlockRequest ret == 4 loop stays the backstop.
        self.fakes["cybos"].GetLimitRemainCount = None
        place_order(
            self.session, side="buy", code="005930", qty=10, price=14100,
        )  # must not raise

    def test_cancel_goes_through_gate_too(self) -> None:
        self.fakes["cybos"].limit_remain_count = 3
        cancel_order(self.session, order_no="777777", code="005930", qty=None)
        self.assertEqual(self.fakes["cancel"].inputs[5], 0)


class PositionQueriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.fakes = support.fake_session()
        wc = support.client()
        obj = wc.make("CpTrade.CpTd6033")
        obj.headers[7] = 1
        obj.rows[0] = ["Samsung"]
        obj.rows[1] = [" "]
        obj.rows[2] = [0]
        obj.rows[7] = [100]          # executed balance qty
        obj.rows[9] = [1600000]      # eval amount
        obj.rows[11] = [100000]      # eval pnl
        obj.rows[12] = ["A005930"]
        obj.rows[15] = [100]         # sellable
        obj.rows[17] = [15000]       # executed book price
        obj._continue = False
        self.obj = obj

    def test_positions(self) -> None:
        positions = fetch_positions(self.session)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["code"], "005930")
        self.assertEqual(positions[0]["qty"], 100)
        self.assertEqual(positions[0]["avg_price"], 15000)
        self.assertEqual(positions[0]["eval_pnl"], 100000)

    def test_balance_aggregation(self) -> None:
        balance = fetch_balance(self.session)
        self.assertEqual(balance["positions_count"], 1)
        self.assertEqual(balance["total_buy_amount"], 15000 * 100)
        self.assertEqual(balance["total_eval_amount"], 1600000)
        self.assertEqual(balance["total_eval_pnl"], 100000)
        # Header/fallback fields: fake headers are 0 so equity degrades to
        # cash(0) + total_eval; buyable fake succeeds with 0 values.
        self.assertEqual(balance["equity"], 1600000)
        self.assertEqual(balance["cash"], 0)
        self.assertEqual(balance["d2_deposit"], 0)
        self.assertNotIn("buyable_error", balance)

    def test_balance_header_and_buyable_cash_fields(self) -> None:
        # CpTd6033 official headers
        self.obj.headers[3] = 15000000          # 총평가금액
        self.obj.headers[9] = 2000000           # D+2 예상예수금
        self.obj.headers[6] = 0                 # 대출금액
        # CpTdNew5331A buyable query
        wc = support.client()
        buyable = wc.make("CpTrade.CpTdNew5331A")
        buyable.headers[10] = 8000000           # 현금주문가능금액
        buyable.headers[45] = 9000000           # 예수금
        buyable.headers[47] = 8000000           # 가능예수금

        balance = fetch_balance(self.session)
        # Equity = orderable cash + stock eval (NOT CpTd6033 header 3, which
        # live measurement on 2026-09-10 showed to be the stock evaluation
        # alone -- using it as equity would fake an ~88 % daily loss).
        self.assertEqual(balance["equity"], 8000000 + 1600000)
        self.assertEqual(balance["broker_total_eval"], 15000000)
        self.assertEqual(balance["d2_deposit"], 2000000)
        self.assertEqual(balance["cash"], 8000000)
        self.assertEqual(balance["deposit"], 9000000)
        self.assertEqual(balance["usable_deposit"], 8000000)
        self.assertEqual(balance["withdrawable"], 8000000)
        # buyable query shaped for an amount-only query (no stock code)
        self.assertEqual(buyable.inputs[0], support.ACC)
        self.assertEqual(buyable.inputs[1], "01")
        self.assertEqual(buyable.inputs[2], "")
        self.assertEqual(buyable.inputs[6], "1")

    def test_balance_buyable_failure_falls_back_to_d2(self) -> None:
        self.obj.headers[9] = 5000000           # D+2 예상예수금 (fallback source)
        buyable = support.client().make("CpTrade.CpTdNew5331A")
        buyable.block_ret = 1                   # BlockRequest fails

        balance = fetch_balance(self.session)
        self.assertEqual(balance["cash"], 5000000)      # degraded to d2
        self.assertEqual(balance["equity"], 5000000 + 1600000)  # cash+total_eval
        self.assertIn("buyable_error", balance)
        self.assertNotIn("deposit", balance)


class DailyPnlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.fakes = support.fake_session()
        obj = support.client().make("CpTrade.CpTd6032")
        obj.headers[0] = 1
        obj.headers[1] = 1000   # balance eval pnl
        obj.headers[2] = 200    # sell realized pnl
        obj.headers[3] = 3.5    # return rate
        obj.rows[0] = ["Samsung"]
        obj.rows[5] = [100]     # today balance
        obj.rows[8] = [60000]   # current
        obj.rows[12] = ["A005930"]
        obj._continue = False

    def test_daily_pnl_summary_and_items(self) -> None:
        data = fetch_daily_pnl(self.session)
        self.assertEqual(data["summary"]["eval_pnl"], 1000)
        self.assertEqual(data["summary"]["realized_pnl"], 200)
        self.assertEqual(data["items"][0]["code"], "005930")


class QuoteFetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.fakes = support.fake_session()
        obj = support.client().make("DsCbo1.StockMst")
        obj.headers[0] = "A005930"
        obj.headers[1] = "Samsung"
        obj.headers[4] = "150000"
        obj.headers[11] = 60000
        obj.headers[12] = 1000
        obj.headers[18] = 123456

    def test_quote_fetch(self) -> None:
        quotes = fetch_quotes(self.session, ["005930"])
        q = quotes["005930"]
        self.assertEqual(q["current"], 60000)
        self.assertEqual(q["name"], "Samsung")
        self.assertEqual(self.fakes["order"].inputs.get(3), None)  # untouched


class QuoteRequestBudgetTest(unittest.TestCase):
    """Quotes spend Daishin's *quote* budget: pace them, never send duplicates.

    Exceeding the per-second quote budget gets the client blocked, so every
    snapshot must pass the ``GetLimitRemainCount(1)`` gate first and repeated
    codes must collapse to one broker request.
    """

    def setUp(self) -> None:
        self.session, self.fakes = support.fake_session()
        self.mst = support.client().make("DsCbo1.StockMst")
        self.mst.headers[0] = "A005930"
        self.mst.headers[11] = 60000

    def test_duplicate_codes_cost_one_request(self) -> None:
        quotes = fetch_quotes(self.session, ["005930", "A005930", "005930"])
        self.assertEqual(self.mst.block_calls, 1)
        self.assertEqual(list(quotes), ["005930"])

    def test_distinct_codes_each_cost_one_request(self) -> None:
        fetch_quotes(self.session, ["005930", "000660"])
        self.assertEqual(self.mst.block_calls, 2)

    def test_quotes_go_through_the_quote_budget_gate(self) -> None:
        cybos = self.fakes["cybos"]
        cybos.limit_remain_count = 0          # quote budget exhausted
        cybos.LimitRequestRemainTime = 60000  # resets in 60s > gate timeout

        with self.assertRaises(BridgeError) as cm:
            fetch_quotes(self.session, ["005930"])
        self.assertEqual(cm.exception.code, "rate_limited")
        self.assertIn("quote", cm.exception.message)
        self.assertEqual(self.mst.block_calls, 0, "requested despite empty budget")

    def test_order_gate_does_not_consume_quote_budget(self) -> None:
        """place_order is an order-budget request; it must not be gated on quotes."""
        self.fakes["cybos"].limit_remain_count = 1
        result = place_order(
            self.session, side="buy", code="005930", qty=1, price=100
        )
        self.assertEqual(result["code"], "005930")


if __name__ == "__main__":
    unittest.main()
