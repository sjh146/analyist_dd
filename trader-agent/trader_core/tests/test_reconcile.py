"""Tests for journal <-> broker reconciliation (:mod:`trader_core.reconcile`).

Built from the real incident on 2026-09-10: a stop-out was journaled at the
market-order echo price 0, and the recorded loss (-121,500 KRW) was wrong.
Reconciliation must repair that from broker facts, never from guesses.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from trader_core.journal import Journal
from trader_core.reconcile import reconcile


def _position(code="026910", qty=30, avg=4045.0, name="광진실업"):
    return {"code": code, "name": name, "qty": qty, "avg_price": avg,
            "sellable_qty": qty}


class ReconcileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.journal = Journal(os.path.join(self._tmp.name, "j.sqlite3"))

    def tearDown(self) -> None:
        close = getattr(self.journal, "close", None)
        if callable(close):
            close()
        self._tmp.cleanup()

    def _open_trade(self, code="026910", qty=30, price=4045.0):
        return self.journal.record_trade(
            side="buy", code=code, qty=qty, price=price, name="광진실업",
            entry_reason="rank", stop_pct=0.07, tp_pct=0.15,
        )

    # 1) journal open, broker flat --------------------------------------- #
    def test_broker_flat_closes_at_the_live_quote(self) -> None:
        self._open_trade()
        result = reconcile(
            self.journal, [], quotes={"026910": {"current": 3755}},
            apply=True,
        )
        self.assertEqual(len(result["closed"]), 1)
        self.assertAlmostEqual(result["closed"][0]["pnl"], -8700.0)
        self.assertEqual(self.journal.open_trades(), [])
        closed = self.journal.closed_trades(30)
        self.assertEqual(closed[0]["exit_price"], 3755.0)  # never 0
        self.assertIn("broker flat", closed[0]["exit_reason"])

    def test_report_only_changes_nothing(self) -> None:
        self._open_trade()
        result = reconcile(self.journal, [], quotes={"026910": {"current": 3755}})
        self.assertFalse(result["applied"])
        self.assertEqual(len(result["closed"]), 1)
        self.assertEqual(len(self.journal.open_trades()), 1)  # untouched

    def test_flat_close_can_be_reported_without_being_applied(self) -> None:
        """``apply_flat_close=False`` never guesses a fill price from a quote.

        The runner's post-close pass uses this: an unfilled order is repaired
        (the broker is the authority), but a journal row whose position simply
        vanished is only reported until a real fill price is known.
        """
        self._open_trade()
        result = reconcile(
            self.journal,
            [],
            quotes={"026910": {"current": 3755}},
            apply=True,
            apply_flat_close=False,
        )
        self.assertEqual(len(result["closed"]), 1)  # still reported
        self.assertEqual(len(self.journal.open_trades()), 1, "no invented exit")

        # ... while an unfilled order is still repaired in the same call.
        self.journal.close_trade(
            self.journal.open_trades()[0]["id"], exit_price=4045.0,
            exit_reason="cleanup",
        )
        self._open_trade(code="424980", qty=12, price=9700.0)
        order = {"order_no": 3729, "code": "424980", "qty": 12, "filled_qty": 0, "cancelable_qty": 0}
        result = reconcile(
            self.journal, [], open_orders=[order], apply=True,
            apply_flat_close=False,
        )
        self.assertEqual(len(result["unfilled"]), 1)
        closed = self.journal.closed_trades(30)
        phantom = [r for r in closed if r["code"] == "424980"][0]
        self.assertEqual(phantom["pnl"], 0.0)
        self.assertIn("never filled", phantom["exit_reason"])

    def test_without_a_price_it_reports_and_waits(self) -> None:
        self._open_trade()
        result = reconcile(self.journal, [], quotes={}, apply=True)
        self.assertEqual(result["closed"], [])
        self.assertEqual(len(result["needs_price"]), 1)
        self.assertEqual(len(self.journal.open_trades()), 1)
        self.assertIn("no price is available", result["notes"][0])

    def test_explicit_exit_price_wins(self) -> None:
        self._open_trade()
        result = reconcile(
            self.journal, [], quotes={"026910": {"current": 3755}},
            exit_price=3700.0, apply=True,
        )
        self.assertEqual(self.journal.closed_trades(30)[0]["exit_price"], 3700.0)

    # 2) journal closed, broker still holds ------------------------------ #
    def test_phantom_close_is_reopened(self) -> None:
        trade_id = self._open_trade()
        self.journal.close_trade(trade_id, exit_price=0.0, exit_reason="monitor stop")
        self.assertEqual(len(self.journal.closed_trades(30)), 1)

        result = reconcile(self.journal, [_position()], apply=True)
        self.assertEqual(len(result["reopened"]), 1)
        self.assertEqual(result["reopened"][0]["was_exit_price"], 0.0)
        self.assertEqual(len(self.journal.closed_trades(30)), 0)
        open_rows = self.journal.open_trades()
        self.assertEqual(len(open_rows), 1)
        self.assertEqual(open_rows[0]["code"], "026910")

    # 3) broker holds it, journal knows nothing -------------------------- #
    def test_missing_entry_is_adopted_from_the_broker(self) -> None:
        result = reconcile(self.journal, [_position()], apply=True)
        self.assertEqual(len(result["adopted"]), 1)
        self.assertEqual(result["adopted"][0]["entry"], 4045.0)
        rows = self.journal.open_trades()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 4045.0)
        self.assertEqual(rows[0]["qty"], 30)
        self.assertIn("adopted from broker", rows[0]["entry_reason"])

    # 4) already consistent ---------------------------------------------- #
    def test_matching_state_reports_nothing_to_do(self) -> None:
        self._open_trade()
        result = reconcile(self.journal, [_position()], apply=True)
        self.assertEqual(result["reopened"], [])
        self.assertEqual(result["closed"], [])
        self.assertEqual(result["adopted"], [])
        self.assertEqual(result["needs_price"], [])
        self.assertEqual(len(self.journal.open_trades()), 1)

    def test_a_prefix_is_normalised(self) -> None:
        self._open_trade()
        position = _position()
        position["code"] = "A026910"
        result = reconcile(self.journal, [position], apply=True)
        self.assertEqual(result["adopted"], [])  # recognised as held
        self.assertEqual(result["closed"], [])

    # 5) journal open, broker order unfilled ----------------------------- #
    # 2026-10-06: 계류 주문은 정정 금지로 규칙이 바뀌었다 → 이 테스트는 '거부된' 주문 케이스다.
    def test_rejected_order_is_closed_at_cost(self) -> None:
        self._open_trade(code="424980", qty=12, price=9700.0)
        order = {"order_no": 3729, "code": "424980", "qty": 12, "filled_qty": 0,
                 "price": 9700, "cancelable_qty": 0}
        result = reconcile(self.journal, [], open_orders=[order], apply=True)
        self.assertEqual(len(result["unfilled"]), 1)
        self.assertEqual(self.journal.open_trades(), [])
        closed = self.journal.closed_trades(30)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["exit_price"], 9700.0)
        self.assertEqual(closed[0]["pnl"], 0.0)  # never a position: zero PnL
        self.assertIn("never filled", closed[0]["exit_reason"])

    def test_partially_filled_order_is_not_treated_as_unfilled(self) -> None:
        """A partially filled order is not a phantom order.

        With the broker reporting no position the journal row is still closed
        (rule 1), but it must never be classified as "order never filled".
        """
        self._open_trade(code="424980", qty=12, price=9700.0)
        order = {"order_no": 3729, "code": "424980", "qty": 12, "filled_qty": 5}
        result = reconcile(
            self.journal, [], open_orders=[order],
            quotes={"424980": {"current": 9700}}, apply=True,
        )
        self.assertEqual(result["unfilled"], [])
        self.assertEqual(len(result["closed"]), 1)
        self.assertEqual(result["closed"][0]["exit"], 9700.0)

    # 4) journal closed at a bogus price --------------------------------- #
    def test_bogus_exit_is_corrected_with_a_broker_price(self) -> None:
        trade_id = self._open_trade()
        self.journal.close_trade(trade_id, exit_price=0.0, exit_reason="monitor stop")
        self.assertEqual(self.journal.closed_trades(30)[0]["pnl"], -121350.0)

        result = reconcile(
            self.journal, [], exit_prices={"026910": 3747.0}, apply=True
        )
        self.assertEqual(len(result["corrected"]), 1)
        self.assertAlmostEqual(result["corrected"][0]["pnl"], -8940.0)
        row = self.journal.closed_trades(30)[0]
        self.assertEqual(row["exit_price"], 3747.0)
        self.assertAlmostEqual(row["pnl"], -8940.0)
        self.assertIn("price corrected by reconcile", row["exit_reason"])

    def test_bogus_exit_without_a_price_is_only_reported(self) -> None:
        trade_id = self._open_trade()
        self.journal.close_trade(trade_id, exit_price=0.0, exit_reason="monitor stop")
        result = reconcile(self.journal, [], apply=True)
        self.assertEqual(len(result["bogus_exits"]), 1)
        self.assertEqual(result["bogus_exits"][0]["pnl_wrong"], -121350.0)
        # untouched: the journal still says 0 until a real price is supplied
        self.assertEqual(self.journal.closed_trades(30)[0]["exit_price"], 0.0)


class PartialFillTest(unittest.TestCase):
    """Rule 6: the broker's fill is the fact, the order echo is not."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.journal = Journal(os.path.join(self._tmp.name, "j.sqlite3"))

    def tearDown(self) -> None:
        self.journal.close()
        self._tmp.cleanup()

    def _open(self, qty: int = 37, price: float = 3300.0) -> int:
        return self.journal.record_trade(
            side="buy", code="073010", qty=qty, price=price, name="케이에스피",
            entry_reason="rank", stop_pct=0.07, tp_pct=0.15,
        )

    def test_journal_qty_is_corrected_to_the_broker_fill(self) -> None:
        self._open()
        result = reconcile(
            self.journal,
            [_position(code="073010", qty=3, avg=3300.33, name="케이에스피")],
            apply=True,
        )
        self.assertEqual(len(result["qty_fixed"]), 1)
        row = self.journal.open_trade_for("073010")
        self.assertEqual(row["qty"], 3)
        self.assertAlmostEqual(row["price"], 3300.33, places=2)
        self.assertIn("partial fill", row["entry_reason"])
        self.assertIn("073010", result["notes"][0])

    def test_matching_qty_is_left_untouched(self) -> None:
        self._open(qty=34, price=3605.0)
        result = reconcile(
            self.journal,
            [_position(code="073010", qty=34, avg=3605.5, name="케이에스피")],
            apply=True,
        )
        self.assertEqual(result["qty_fixed"], [])
        self.assertEqual(self.journal.open_trade_for("073010")["qty"], 34)

    def test_dry_run_reports_without_changing(self) -> None:
        self._open()
        result = reconcile(
            self.journal,
            [_position(code="073010", qty=3, avg=3300.33, name="케이에스피")],
        )
        self.assertEqual(len(result["qty_fixed"]), 1)
        self.assertEqual(self.journal.open_trade_for("073010")["qty"], 37)

    def test_reopened_row_converges_on_the_second_pass(self) -> None:
        """A phantom close is reopened before the qty rule can see it.

        Rule 2 (reopen) runs before rule 6 (qty fix), so a row that was closed
        keeps its old qty for one pass; the next reconcile corrects it.  Two
        passes converge -- worth pinning so a caller knows to re-run.
        """
        trade_id = self._open()
        self.journal.close_trade(
            trade_id=trade_id, exit_price=3400.0, exit_reason="take profit"
        )
        positions = [_position(code="073010", qty=3, avg=3300.33, name="케이에스피")]
        first = reconcile(self.journal, positions, apply=True)
        self.assertEqual(len(first["reopened"]), 1)
        self.assertEqual(first["qty_fixed"], [])
        self.assertEqual(self.journal.open_trade_for("073010")["qty"], 37)

        second = reconcile(self.journal, positions, apply=True)
        self.assertEqual(len(second["qty_fixed"]), 1)
        row = self.journal.open_trade_for("073010")
        self.assertEqual(row["qty"], 3)
        self.assertAlmostEqual(row["price"], 3300.33, places=2)


if __name__ == "__main__":
    unittest.main()


class RestingOrderTest(unittest.TestCase):
    """2026-10-06 실사고: 체결 0 이어도 취소가능 수량이 남은 주문은 **계류**다 — 정정 금지.

    실측: 319400 은 filled_qty=0 · cancelable_qty=1 인 살아있는 주문이었는데 '미체결 정리'로
    원가 청산해버렸고, 그 뒤 실제 체결되며 저널(닫힘)/브로커(보유)가 반대로 어긋났다.
    """

    def test_resting_order_is_not_closed(self) -> None:
        import datetime as _dt
        from trader_core import Journal
        from trader_core.reconcile import reconcile
        kst = _dt.timezone(_dt.timedelta(hours=9))
        ts = _dt.datetime(2026, 10, 6, 9, 0, 7, tzinfo=kst)
        journal = Journal(":memory:")
        journal.record_trade(side="buy", code="319400", qty=1, price=20800.0, ts=ts)
        order = {"order_no": 8629, "code": "319400", "qty": 1, "filled_qty": 0,
                 "cancelable_qty": 1}
        res = reconcile(journal, broker_positions=[], quotes=None, open_orders=[order],
                        apply=True)
        self.assertEqual(res["unfilled"], [])                       # 계류는 정리 대상 아님
        self.assertEqual([r["code"] for r in journal.closed_on("2026-10-06")], [])  # 저널도 열린 채
