"""Pure risk math tests (Kelly, caps, stop/tp, trailing, guards, CWE-20)."""

from __future__ import annotations

import unittest

from trader_core import risk


class KellyTest(unittest.TestCase):
    def test_kelly_formula(self) -> None:
        # p=0.6, b=avg_win/avg_loss=2 -> f* = 0.6 - 0.4/2 = 0.4
        self.assertAlmostEqual(risk.kelly_fraction(0.6, 2.0, 1.0), 0.4)
        # p=0.55, b=1 -> f* = 0.1
        self.assertAlmostEqual(risk.kelly_fraction(0.55, 1.0, 1.0), 0.1)
        # break-even p=0.5, b=1 -> 0
        self.assertEqual(risk.kelly_fraction(0.5, 1.0, 1.0), 0.0)

    def test_kelly_guards(self) -> None:
        self.assertEqual(risk.kelly_fraction(1.0, 2.0, 1.0), 0.0)  # p>=1
        self.assertEqual(risk.kelly_fraction(0.0, 2.0, 1.0), 0.0)  # p<=0
        self.assertEqual(risk.kelly_fraction(0.6, 1.0, 0.0), 0.0)  # avg_loss<=0
        self.assertEqual(risk.kelly_fraction(0.6, 0.0, 1.0), 0.0)  # avg_win<=0
        self.assertEqual(risk.kelly_fraction(0.6, -2.0, 1.0), 0.0)  # b<=0
        self.assertGreaterEqual(risk.kelly_fraction(0.9, 3.0, 1.0), 0.0)

    def test_sized_qty_kellysizer_cap(self) -> None:
        # deploy 1.0, cap 0.25: f*=0.4 -> deploy 0.25 -> 250,000 KRW
        qty, invest = risk.sized_qty(
            equity=1_000_000, price=1000, win_rate=0.6, avg_win=2.0,
            avg_loss=1.0, max_position_pct=1.0,
        )
        self.assertEqual(qty, 250)
        self.assertEqual(invest, 250_000)

    def test_sized_qty_fractional_kelly(self) -> None:
        # deploy 0.5 of f*=0.4 -> 0.2 -> 200,000
        qty, invest = risk.sized_qty(
            equity=1_000_000, price=1000, win_rate=0.6, avg_win=2.0,
            avg_loss=1.0, kelly_fraction=0.5, max_position_pct=1.0,
        )
        self.assertEqual(qty, 200)
        self.assertEqual(invest, 200_000)

    def test_sized_qty_tighter_cap(self) -> None:
        qty, _ = risk.sized_qty(
            equity=1_000_000, price=1000, win_rate=0.6, avg_win=2.0,
            avg_loss=1.0, kelly_cap=0.05, max_position_pct=1.0,
        )
        self.assertEqual(qty, 50)

    def test_sized_qty_max_order_amount(self) -> None:
        qty, invest = risk.sized_qty(
            equity=1_000_000, price=1000, win_rate=0.6, avg_win=2.0,
            avg_loss=1.0, max_order_amount=100_000, max_position_pct=1.0,
        )
        self.assertEqual(qty, 100)
        self.assertEqual(invest, 100_000)

    def test_sized_qty_position_cap(self) -> None:
        # max_position_pct=0.1 caps budget below the Kelly budget
        qty, invest = risk.sized_qty(
            equity=1_000_000, price=1000, win_rate=0.6, avg_win=2.0,
            avg_loss=1.0, max_position_pct=0.1,
        )
        self.assertEqual(qty, 100)
        self.assertEqual(invest, 100_000)

    def test_sized_qty_rounds_to_zero(self) -> None:
        qty, invest = risk.sized_qty(
            equity=1_000, price=10_000, win_rate=0.6, avg_win=2.0,
            avg_loss=1.0,
        )
        self.assertEqual((qty, invest), (0, 0))

    def test_sized_qty_bad_inputs(self) -> None:
        self.assertEqual(risk.sized_qty(0, 1000, 0.6, 2, 1), (0, 0))
        self.assertEqual(risk.sized_qty(1_000_000, 0, 0.6, 2, 1), (0, 0))
        self.assertEqual(risk.sized_qty(1_000_000, 1000, 0.5, 1, 1), (0, 0))


class StopLossTest(unittest.TestCase):
    def setUp(self) -> None:
        self.position = {"avg_price": 10000, "qty": 10}

    def test_stop_triggers(self) -> None:
        self.assertEqual(risk.should_stop(self.position, 9300, 0.07, 0.15), "stop")
        self.assertEqual(risk.should_stop(self.position, 9299, 0.07, 0.15), "stop")

    def test_take_profit_triggers(self) -> None:
        self.assertEqual(risk.should_stop(self.position, 11500, 0.07, 0.15), "tp")
        self.assertEqual(risk.should_stop(self.position, 11499, 0.07, 0.15), None)

    def test_hold_in_band(self) -> None:
        self.assertIsNone(risk.should_stop(self.position, 10000, 0.07, 0.15))
        self.assertIsNone(risk.should_stop(self.position, 10500, 0.07, 0.15))

    def test_missing_entry_never_triggers(self) -> None:
        self.assertIsNone(risk.should_stop({"qty": 10}, 8000, 0.07, 0.15))
        self.assertIsNone(risk.should_stop(self.position, 0, 0.07, 0.15))
        self.assertIsNone(risk.should_stop(self.position, -5, 0.07, 0.15))

    def test_trailing_stop(self) -> None:
        self.assertFalse(risk.check_trailing_stop(9500, 10000, 0.07))
        self.assertTrue(risk.check_trailing_stop(9200, 10000, 0.07))
        # new high ratchets the stop up
        self.assertFalse(risk.check_trailing_stop(10500, 10000, 0.07))


class BalancePositionGuardTest(unittest.TestCase):
    def test_check_buyable(self) -> None:
        self.assertTrue(risk.check_buyable(10_000, 9_000))
        self.assertFalse(risk.check_buyable(10_000, 9_000, reserve=2_000))
        self.assertFalse(risk.check_buyable(0, 100))
        self.assertFalse(risk.check_buyable(10_000, 0))
        self.assertFalse(risk.check_buyable("bad", 100))

    def test_check_position_limit(self) -> None:
        # held 50 + existing 10 + additional 40 = 100 @ 1000 = 100k <= 20% of 1M
        self.assertTrue(risk.check_position_limit(
            held_qty=50, existing_qty=10, additional=40,
            max_position_pct=0.20, equity=1_000_000, price=1000))
        # 200 additional pushes total to 260k > 200k
        self.assertFalse(risk.check_position_limit(
            held_qty=50, existing_qty=10, additional=200,
            max_position_pct=0.20, equity=1_000_000, price=1000))
        # no price/equity -> fail closed
        self.assertFalse(risk.check_position_limit(
            held_qty=1, existing_qty=0, additional=1,
            max_position_pct=0.2, equity=1_000_000, price=0))
        self.assertFalse(risk.check_position_limit(
            held_qty=1, existing_qty=0, additional=1,
            max_position_pct=0.2, equity=0, price=1000))
        # nothing to add is trivially fine
        self.assertTrue(risk.check_position_limit(
            held_qty=1000, existing_qty=0, additional=0,
            max_position_pct=0.2, equity=1_000_000, price=1000))

    def test_check_daily_trade_count(self) -> None:
        self.assertTrue(risk.check_daily_trade_count(3, 5))
        self.assertFalse(risk.check_daily_trade_count(5, 5))
        self.assertFalse(risk.check_daily_trade_count(0, 0))


class MarketOrderGuardTest(unittest.TestCase):
    def test_market_requires_reference(self) -> None:
        self.assertTrue(risk.validate_market_order(None, 10000, 0.10))
        self.assertFalse(risk.validate_market_order(None, None, 0.10))
        self.assertFalse(risk.validate_market_order(None, 0, 0.10))

    def test_price_inside_band(self) -> None:
        self.assertTrue(risk.validate_market_order(11000, 10000, 0.10))
        self.assertFalse(risk.validate_market_order(12000, 10000, 0.10))
        self.assertFalse(risk.validate_market_order(0, 10000, 0.10))
        self.assertFalse(risk.validate_market_order(-5, 10000, 0.10))
        self.assertFalse(risk.validate_market_order(10000, "bad", 0.10))


if __name__ == "__main__":
    unittest.main()
