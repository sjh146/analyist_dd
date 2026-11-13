"""Exit journaling: a market sell must never be recorded at price 0.

Found live 2026-09-10 on the paper account: position 026910 was stopped out
(quote 3,755 vs entry 4,045) but the journal close used the bridge's market
order echo of ``price: 0``, booking the whole position as a -121,500 KRW loss
instead of the ~-8,400 KRW the stop implied.  A 0 price is an *unknown* price,
never a real fill, so the engine must fall back to the market reference quote.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from trader_core.config import Config
from trader_core.engine import OrderPlan, TraderEngine
from trader_core.journal import Journal


class _EchoZeroBridge:
    """Minimal bridge double whose market orders echo ``price: 0``."""

    def __init__(self, quote_price: float = 3755.0) -> None:
        self.quote_price = quote_price
        self.orders = []

    # -- quotes / account surface used by the engine -------------------- #
    def quotes(self, codes):
        return {code: {"current": self.quote_price} for code in codes}

    def balance(self):
        return {"cash": 1_000_000, "equity": 1_000_000}

    def positions(self):
        return []

    def open_orders(self):
        return []

    def order(self, side, code, qty, price=None, order_type="limit"):
        self.orders.append(
            {"side": side, "code": code, "qty": qty, "price": price,
             "order_type": order_type}
        )
        return {
            "order_no": 0,
            "side": side,
            "code": code,
            "qty": qty,
            "price": 0,          # <-- the real bridge echoes 0 for market orders
            "order_type": order_type,
            "message": "",
        }


class ExitJournalPriceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.journal = Journal(os.path.join(self._tmp.name, "j.sqlite3"))
        self.bridge = _EchoZeroBridge(quote_price=3755.0)
        self.engine = TraderEngine(
            self.bridge,
            feed_client=None,
            config=Config(dry_run=False, market_hours_only=False),
            journal=self.journal,
        )
        self.journal.record_trade(
            side="buy", code="026910", qty=30, price=4045.0, name="광진실업",
            entry_reason="rank", stop_pct=0.07, tp_pct=0.15,
        )

    def tearDown(self) -> None:
        close = getattr(self.journal, "close", None)
        if callable(close):
            close()
        self._tmp.cleanup()

    def test_market_exit_is_journaled_at_the_reference_quote(self) -> None:
        plan = OrderPlan(
            side="sell", code="026910", name="광진실업", qty=30,
            price=None, order_type="market", entry_reason="monitor stop",
        )
        self.engine.execute(plan)

        self.assertEqual(len(self.bridge.orders), 1)
        self.assertEqual(self.bridge.orders[0]["side"], "sell")
        closed = self.journal.closed_trades(30)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["exit_price"], 3755.0)   # not 0
        pnl = float(closed[0]["pnl"])
        # (3755 - 4045) * 30 = -8,700 -- the stop loss, not the whole position.
        self.assertAlmostEqual(pnl, -8700.0)
        self.assertAlmostEqual(
            float(closed[0]["price"]) * float(closed[0]["qty"]), 121350.0
        )

    def test_no_reference_quote_blocks_the_market_exit(self) -> None:
        """Fail closed: an unpriced market order is never placed."""
        self.bridge.quote_price = 0.0
        plan = OrderPlan(
            side="sell", code="026910", name="광진실업", qty=30,
            price=None, order_type="market", entry_reason="monitor stop",
        )
        with self.assertRaises(Exception):
            self.engine.execute(plan)
        self.assertEqual(self.bridge.orders, [])
        self.assertEqual(self.journal.closed_trades(30), [])


if __name__ == "__main__":
    unittest.main()
