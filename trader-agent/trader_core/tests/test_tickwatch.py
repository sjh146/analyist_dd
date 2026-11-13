"""Tests for the sub-cycle exit watch (realtime cache -> exit candidates).

The watch exists so a stop / trailing stop can be honoured within ~1 s instead
of waiting for the next loop cycle.  Two properties matter more than speed and
are pinned here: a *stale* tick must never fire, and a tick that the broker mark
does not confirm must never fire (the 2026-09-10 divergence left a -17.8 %
position un-stopped while the cached quote still showed -1 %).
"""

from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta, timezone

from trader_core.tickwatch import plain_code, tick_age, tick_exit_candidates

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 22, 10, 0, tzinfo=KST)


def _row(code="005930", price=10000.0, stop=0.10, tp=0.10, hwm=None):
    return {
        "code": code,
        "price": price,
        "stop_pct": stop,
        "tp_pct": tp,
        "hwm_price": hwm,
    }


def _position(code="005930", qty=100):
    return {"code": code, "qty": qty, "sellable_qty": qty}


def _tick(price, ts=None):
    return {"code": "005930", "current": price, "ts": time.time() if ts is None else ts}


class CandidateTest(unittest.TestCase):
    def test_stop_breach_fires(self) -> None:
        breaches = tick_exit_candidates(
            [_position()], [_row()], {"005930": _tick(8950.0)}, now=NOW
        )
        self.assertEqual(len(breaches), 1)
        self.assertEqual(breaches[0].reason, "stop")
        self.assertEqual(breaches[0].qty, 100)

    def test_price_inside_the_band_holds(self) -> None:
        breaches = tick_exit_candidates(
            [_position()], [_row()], {"005930": _tick(9500.0)}, now=NOW
        )
        self.assertEqual(breaches, [])

    def test_take_profit_fires(self) -> None:
        breaches = tick_exit_candidates(
            [_position()], [_row()], {"005930": _tick(11100.0)}, now=NOW
        )
        self.assertEqual([b.reason for b in breaches], ["tp"])

    def test_trailing_stop_fires_after_the_peak_gives_back(self) -> None:
        # Peaked at +8 % (hwm), now +2.4 %: armed (+5.5 %) and 5 % off the peak.
        breaches = tick_exit_candidates(
            [_position()], [_row(stop=0.30, tp=0.30, hwm=10800.0)],
            {"005930": _tick(10240.0)}, now=NOW,
            arm_pct=0.055, trail_pct=0.05,
        )
        self.assertEqual(len(breaches), 1)
        self.assertIn("trailing stop", breaches[0].reason)

    def test_trailing_stop_not_armed_below_the_arm(self) -> None:
        # Peak +3 % only: the fixed stop still governs, no trail exit.
        breaches = tick_exit_candidates(
            [_position()], [_row(stop=0.30, tp=0.30, hwm=10300.0)],
            {"005930": _tick(9790.0)}, now=NOW,
            arm_pct=0.055, trail_pct=0.05,
        )
        self.assertEqual(breaches, [])

    def test_live_tick_makes_the_peak_conservative(self) -> None:
        """The peak used is ``max(stored hwm, live price)``: a stored peak above
        the live price keeps the trail armed (the monitor cycle may have seen a
        high the cache tick has not repeated)."""
        breaches = tick_exit_candidates(
            [_position()], [_row(stop=0.30, tp=0.30, hwm=10580.0)],
            {"005930": _tick(10040.0)}, now=NOW,
            arm_pct=0.055, trail_pct=0.05,
        )
        self.assertEqual([b.reason for b in breaches], ["trailing stop (-5% from peak 10,580)"])

    def test_stale_tick_is_ignored(self) -> None:
        stale = _tick(8000.0, ts=NOW.timestamp() - 60)
        breaches = tick_exit_candidates(
            [_position()], [_row()], {"005930": stale}, now=NOW, max_age_seconds=5.0
        )
        self.assertEqual(breaches, [])

    def test_tick_without_a_timestamp_is_ignored(self) -> None:
        breaches = tick_exit_candidates(
            [_position()], [_row()], {"005930": {"current": 8000.0}}, now=NOW
        )
        self.assertEqual(breaches, [])

    def test_missing_tick_holds(self) -> None:
        self.assertEqual(tick_exit_candidates([_position()], [_row()], {}, now=NOW), [])

    def test_position_without_a_journal_row_is_ignored(self) -> None:
        breaches = tick_exit_candidates(
            [_position(code="000660")], [_row()], {"005930": _tick(8000.0)}, now=NOW
        )
        self.assertEqual(breaches, [])

    def test_zero_sellable_quantity_is_ignored(self) -> None:
        breaches = tick_exit_candidates(
            [{"code": "005930", "qty": 0, "sellable_qty": 0}],
            [_row()], {"005930": _tick(8000.0)}, now=NOW,
        )
        self.assertEqual(breaches, [])

    def test_null_price_is_ignored(self) -> None:
        breaches = tick_exit_candidates(
            [_position()], [{"code": "005930", "price": None, "stop_pct": 0.1}],
            {"005930": _tick(8000.0)}, now=NOW,
        )
        self.assertEqual(breaches, [])

    def test_defaults_fill_missing_row_levels(self) -> None:
        breaches = tick_exit_candidates(
            [_position()],
            [{"code": "005930", "price": 10000.0, "stop_pct": None, "tp_pct": None}],
            {"005930": _tick(9200.0)}, now=NOW,
            default_stop_pct=0.07, default_tp_pct=0.07,
        )
        self.assertEqual([b.reason for b in breaches], ["stop"])

    def test_prefixed_codes_are_normalised(self) -> None:
        breaches = tick_exit_candidates(
            [{"code": "A005930", "qty": 3}],
            [{"code": "005930", "price": 10000.0, "stop_pct": 0.10}],
            {"005930": _tick(8000.0)}, now=NOW,
        )
        self.assertEqual(len(breaches), 1)
        self.assertEqual(breaches[0].code, "005930")
        self.assertEqual(breaches[0].qty, 3)

    def test_plain_code_helper(self) -> None:
        self.assertEqual(plain_code("A005930"), "005930")
        self.assertEqual(plain_code("005930"), "005930")
        self.assertIsNone(plain_code("5930"))
        self.assertIsNone(plain_code(None))

    def test_tick_age(self) -> None:
        age = tick_age({"ts": 100.0}, NOW) or 0.0
        self.assertTrue(abs(float(age) - (NOW.timestamp() - 100.0)) < 0.01)
        # A clock going backwards must not produce a negative age.
        self.assertEqual(tick_age({"ts": NOW.timestamp() + 30}, NOW), 0.0)
        self.assertIsNone(tick_age({}, NOW))


if __name__ == "__main__":
    unittest.main()
