"""Tests for the cycle cadence (base interval + intraday focus windows).

The loop must look *often* when the market's attention is concentrated (open,
early afternoon, closing auction) and *cheaply* otherwise -- every cycle costs
broker requests and Daishin blocks clients that over-ask.  These tests pin both
halves of that trade-off.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from runner.config import RunnerConfig
from runner.schedule import DEFAULT_FOCUS_WINDOWS, active_focus, cycle_interval
from trader_core.config import KST


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 10, hour, minute, tzinfo=KST)


class ActiveFocusTest(unittest.TestCase):
    def test_open_window_is_focused(self) -> None:
        focus = active_focus(_at(9, 5))
        self.assertIsNotNone(focus)
        self.assertEqual(focus["window"], "09:00-09:30")

    def test_afternoon_window_is_focused(self) -> None:
        self.assertEqual(active_focus(_at(13, 30))["window"], "13:00-14:00")

    def test_closing_window_is_focused(self) -> None:
        self.assertEqual(active_focus(_at(15, 10))["window"], "14:30-15:30")

    def test_mid_morning_is_not_focused(self) -> None:
        self.assertIsNone(active_focus(_at(10, 30)))

    def test_boundaries_are_inclusive(self) -> None:
        self.assertIsNotNone(active_focus(_at(9, 0)))
        self.assertIsNotNone(active_focus(_at(9, 30)))
        self.assertIsNone(active_focus(_at(9, 31)))

    def test_shortest_interval_wins_on_overlap(self) -> None:
        windows = [
            {"window": "13:00-14:00", "interval_seconds": 120},
            {"window": "13:30-14:00", "interval_seconds": 30},
        ]
        self.assertEqual(active_focus(_at(13, 45), windows)["interval_seconds"], 30)

    def test_malformed_entries_are_ignored(self) -> None:
        windows = [{"window": "nonsense"}, {"interval_seconds": 30}, "junk", None]
        self.assertIsNone(active_focus(_at(10, 0), windows))


class CycleIntervalTest(unittest.TestCase):
    def test_base_interval_outside_focus(self) -> None:
        interval, focus = cycle_interval(_at(11, 0), 300)
        self.assertEqual(interval, 300)
        self.assertIsNone(focus)

    def test_focus_shrinks_the_interval(self) -> None:
        for moment in (_at(9, 10), _at(13, 15)):
            interval, focus = cycle_interval(moment, 300)
            self.assertEqual(interval, 60, moment)
            self.assertIsNotNone(focus)
        # Closing hour watches twice as often as the other focus windows.
        interval, focus = cycle_interval(_at(15, 5), 300)
        self.assertEqual(interval, 30)
        self.assertEqual(focus["window"], "14:30-15:30")

    def test_never_lengthens_a_short_base(self) -> None:
        """A user-configured 30s base must not be stretched by a 60s window."""
        interval, _focus = cycle_interval(_at(9, 10), 30)
        self.assertEqual(interval, 30)

    def test_empty_focus_list_keeps_the_base(self) -> None:
        interval, focus = cycle_interval(_at(9, 10), 300, [])
        self.assertEqual(interval, 300)
        self.assertIsNone(focus)

    def test_defaults_exist_for_the_three_windows(self) -> None:
        windows = [entry["window"] for entry in DEFAULT_FOCUS_WINDOWS]
        self.assertIn("09:00-09:30", windows)
        self.assertIn("13:00-14:00", windows)
        self.assertIn("14:30-15:30", windows)


class FocusWindowConfigTest(unittest.TestCase):
    def test_default_config_has_the_three_focus_windows(self) -> None:
        cfg = RunnerConfig()
        windows = [entry["window"] for entry in cfg.focus_windows]
        self.assertEqual(windows, ["09:00-09:30", "13:00-14:00", "14:30-15:30"])
        closing = [e for e in cfg.focus_windows if e["window"] == "14:30-15:30"][0]
        self.assertEqual(closing["interval_seconds"], 30)

    def test_json_string_from_env_is_parsed(self) -> None:
        cfg = RunnerConfig.from_mapping(
            {
                "focus_windows": '[{"window": "10:00-10:15", '
                                 '"interval_seconds": 30}]'
            }
        )
        self.assertEqual(len(cfg.focus_windows), 1)
        self.assertEqual(cfg.focus_windows[0]["interval_seconds"], 30)

    def test_list_from_config_file_is_accepted(self) -> None:
        cfg = RunnerConfig.from_mapping(
            {"focus_windows": [{"window": "12:00-12:30", "interval_seconds": 45}]}
        )
        self.assertEqual(cfg.focus_windows[0]["window"], "12:00-12:30")

    def test_malformed_value_falls_back_to_defaults(self) -> None:
        cfg = RunnerConfig.from_mapping({"focus_windows": "not json"})
        self.assertEqual(len(cfg.focus_windows), 3)
        cfg = RunnerConfig.from_mapping(
            {"focus_windows": [{"window": "", "interval_seconds": 0}]}
        )
        self.assertEqual(len(cfg.focus_windows), 3)


if __name__ == "__main__":
    unittest.main()
