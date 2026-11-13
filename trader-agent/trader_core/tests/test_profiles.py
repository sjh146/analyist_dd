"""Tests for per-screener strategy profiles.

The point of these profiles is that a candidate list is not a strategy: a
``close`` (종가) candidate must be bought into the close and sold at the next
open, while a ``swing`` candidate is held with a stop/take-profit.  These tests
pin that separation, because getting it wrong means holding an overnight trade
for a week (or dumping a swing name at the bell).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from trader_core.config import KST
from trader_core.profiles import (
    DEFAULT_PROFILES,
    EXIT_NEXT_OPEN,
    EXIT_SAME_DAY_CLOSE,
    EXIT_STOP_TP,
    parse_window,
    profile_for,
    sessions_held,
)


def _at(hour: int, minute: int = 0, day: int = 10) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=KST)


class DefaultsTest(unittest.TestCase):
    def test_close_profile_buys_into_the_close_and_exits_at_the_open(self) -> None:
        profile = profile_for("close")
        self.assertEqual(profile.exit_mode, EXIT_NEXT_OPEN)
        allowed, _ = profile.entry_allowed(_at(14, 55))
        self.assertTrue(allowed)
        allowed, reason = profile.entry_allowed(_at(10, 0))
        self.assertFalse(allowed)
        self.assertIn("entry window", reason)

    def test_swing_profile_enters_any_time_and_uses_stop_tp(self) -> None:
        profile = profile_for("swing")
        self.assertEqual(profile.exit_mode, EXIT_STOP_TP)
        self.assertTrue(profile.entry_allowed(_at(9, 30))[0])
        self.assertTrue(profile.entry_allowed(_at(14, 30))[0])

    def test_daytrading_profile_never_holds_overnight(self) -> None:
        profile = profile_for("daytrading")
        self.assertFalse(profile.hold_overnight)
        self.assertEqual(profile.exit_mode, EXIT_SAME_DAY_CLOSE)
        due, reason = profile.time_exit_due("2026-09-10T10:00:00+09:00", _at(15, 5))
        self.assertTrue(due)
        self.assertIn("same_day_close", reason)

    def test_rank_momentum_keeps_the_current_behaviour(self) -> None:
        profile = profile_for("rank_momentum")
        self.assertEqual(profile.exit_mode, EXIT_STOP_TP)
        self.assertEqual(profile.kelly_fraction, 0.25)
        self.assertTrue(profile.entry_allowed(_at(13, 0))[0])

    def test_unknown_screener_defaults_to_swing_shape(self) -> None:
        """A new analysit_dd list must not inherit the close mechanics."""
        profile = profile_for("analysit_ml_v2")
        self.assertEqual(profile.exit_mode, EXIT_STOP_TP)
        self.assertEqual(profile.name, "analysit_ml_v2")

    def test_overrides_win_over_defaults(self) -> None:
        profile = profile_for(
            "swing", {"swing": {"entry_window": "10:00-11:00", "kelly_fraction": 0.05}}
        )
        self.assertEqual(profile.entry_window, "10:00-11:00")
        self.assertEqual(profile.kelly_fraction, 0.05)
        self.assertEqual(profile.exit_mode, EXIT_STOP_TP)  # untouched default


class EntryWindowTest(unittest.TestCase):
    def test_outside_market_hours_is_blocked(self) -> None:
        profile = profile_for("swing")
        allowed, reason = profile.entry_allowed(_at(8, 30))
        self.assertFalse(allowed)
        self.assertIn("market closed", reason)

    def test_disabled_profile_blocks_entries(self) -> None:
        profile = profile_for("close", {"close": {"enabled": False}})
        allowed, reason = profile.entry_allowed(_at(15, 0))
        self.assertFalse(allowed)
        self.assertIn("disabled", reason)


class NextOpenExitTest(unittest.TestCase):
    def test_close_position_is_not_sold_the_same_session(self) -> None:
        profile = profile_for("close")
        due, reason = profile.time_exit_due(
            "2026-09-10T15:05:00+09:00", _at(15, 20)
        )
        self.assertFalse(due)
        self.assertIn("same session", reason)

    def test_close_position_is_sold_at_the_next_open(self) -> None:
        profile = profile_for("close")
        due, reason = profile.time_exit_due(
            "2026-09-10T15:05:00+09:00", _at(9, 3, day=11)
        )
        self.assertTrue(due)
        self.assertIn("next_open", reason)

    def test_close_position_is_not_sold_before_the_open_window(self) -> None:
        profile = profile_for("close")
        due, _ = profile.time_exit_due("2026-09-10T15:05:00+09:00", _at(9, 40, day=11))
        # Window is 09:00-09:10; 09:40 leftovers must still be reported as due
        # on the next cycle *inside* a window.  Here we assert the window rule.
        self.assertFalse(due)

    def test_missing_entry_timestamp_is_never_due(self) -> None:
        profile = profile_for("close")
        self.assertFalse(profile.time_exit_due(None, _at(9, 3, day=11))[0])
        self.assertFalse(profile.time_exit_due("", _at(9, 3, day=11))[0])

    def test_stop_tp_profiles_never_time_exit(self) -> None:
        profile = profile_for("swing")
        due, reason = profile.time_exit_due("2026-09-01T10:00:00+09:00", _at(14, 0))
        self.assertFalse(due)
        self.assertEqual(reason, "")


class TimeExitLadderTest(unittest.TestCase):
    """The shelf-life rules that keep a momentum slot from going stale."""

    def _profile(self, **overrides):
        cfg = dict(DEFAULT_PROFILES["rank_momentum"])
        cfg.update(overrides)
        return profile_for("rank_momentum", {"rank_momentum": cfg})

    def test_flat_position_is_released_at_the_second_session(self) -> None:
        profile = profile_for("rank_momentum")
        # Entry Mon 2026-09-14, now Tue 15:15 (2nd session), +1% -> dead money.
        due, reason = profile.time_rules_due("2026-09-14T11:00:00+09:00",
                                             _at(15, 15, day=15), 0.01)
        self.assertTrue(due)
        self.assertIn("flat_stop", reason)

    def test_a_working_position_is_not_released(self) -> None:
        profile = profile_for("rank_momentum")
        due, _ = profile.time_rules_due("2026-09-14T11:00:00+09:00",
                                        _at(15, 15, day=15), 0.05)
        self.assertFalse(due)

    def test_dead_money_rule_waits_for_its_session(self) -> None:
        profile = profile_for("rank_momentum")
        # Same entry day (1st session) cannot be due yet.
        due, _ = profile.time_rules_due("2026-09-15T10:00:00+09:00",
                                        _at(15, 15, day=15), 0.0)
        self.assertFalse(due)

    def test_outside_the_close_window_nothing_fires(self) -> None:
        profile = profile_for("rank_momentum")
        due, _ = profile.time_rules_due("2026-09-14T11:00:00+09:00",
                                        _at(11, 0, day=15), 0.0)
        self.assertFalse(due)

    def test_missing_quote_holds_but_the_hard_cap_still_fires(self) -> None:
        profile = profile_for("rank_momentum")
        # gain None (no quote): the gain-dependent rules must fail open ...
        due, reason = profile.time_rules_due("2026-09-15T11:00:00+09:00",
                                             _at(15, 15, day=16), None)
        self.assertFalse(due)
        # ... while the 5-session cap does not depend on a price at all.
        due, reason = profile.time_rules_due("2026-09-14T11:00:00+09:00",
                                             _at(15, 15, day=21), None)
        self.assertTrue(due)
        self.assertIn("max_hold_sessions", reason)

    def test_time_stop_stays_off_by_default(self) -> None:
        """Measured 2026-09-22: cutting every under-performer costs more than
        the time it saves (it also cuts the names that then run)."""
        self.assertEqual(profile_for("rank_momentum").time_stop_sessions, 0)
        profile = self._profile(time_stop_sessions=2, time_stop_gain=0.01)
        due, reason = profile.time_rules_due("2026-09-15T11:00:00+09:00",
                                             _at(15, 15, day=16), -0.03)
        self.assertTrue(due)
        self.assertIn("time_stop", reason)

    def test_missing_entry_timestamp_is_never_due(self) -> None:
        profile = profile_for("rank_momentum")
        self.assertFalse(profile.time_rules_due(None, _at(15, 15, day=21), 0.0)[0])
        self.assertFalse(profile.time_rules_due("", _at(15, 15, day=21), 0.0)[0])

    def test_swing_profile_has_no_time_rules(self) -> None:
        profile = profile_for("swing")
        due, reason = profile.time_rules_due("2026-09-01T10:00:00+09:00",
                                             _at(15, 15), 0.0)
        self.assertFalse(due)
        self.assertEqual(reason, "")

    def test_sessions_held_counts_business_days_from_one(self) -> None:
        self.assertEqual(sessions_held("2026-09-15T10:00:00+09:00", _at(15, 0, day=15)), 1)
        self.assertEqual(sessions_held("2026-09-15T10:00:00+09:00", _at(15, 0, day=16)), 2)
        # Fri -> Mon is the 2nd session, not the 4th.
        self.assertEqual(sessions_held("2026-09-18T10:00:00+09:00", _at(15, 0, day=21)), 2)
        self.assertEqual(sessions_held("garbage", _at(15, 0, day=21)), 0)


class ParseWindowTest(unittest.TestCase):
    def test_parses_and_falls_back(self) -> None:
        start, end = parse_window("14:50-15:25")
        self.assertEqual((start.hour, start.minute), (14, 50))
        self.assertEqual((end.hour, end.minute), (15, 25))
        start, end = parse_window("garbage")
        self.assertEqual((start.hour, end.hour), (9, 15))

    def test_every_default_profile_declares_a_usable_window(self) -> None:
        for name in DEFAULT_PROFILES:
            profile = profile_for(name)
            start, end = parse_window(profile.entry_window)
            self.assertLessEqual(start, end, name)
            self.assertIn(
                profile.exit_mode,
                (EXIT_STOP_TP, EXIT_NEXT_OPEN, EXIT_SAME_DAY_CLOSE),
                name,
            )


if __name__ == "__main__":
    unittest.main()
