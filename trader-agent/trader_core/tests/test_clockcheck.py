"""Tests for the wall-clock sanity guard (:mod:`trader_core.clockcheck`).

The incident: the machine clock ran ~4 hours off, the loop kept trading on a
session model that no longer matched reality, and it ran its day-end logic at
the wrong moment.  Every component reads the same skewed clock, so the only way
to notice is to compare against something external.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from trader_core.clockcheck import (
    ClockGuard,
    blocks_entries,
    parse_http_date,
    skew_seconds,
)

UTC = timezone.utc
SERVER = datetime(2026, 9, 10, 10, 24, 20, tzinfo=UTC)


class ParseHttpDateTest(unittest.TestCase):
    def test_rfc1123_header(self) -> None:
        parsed = parse_http_date("Thu, 10 Sep 2026 10:24:20 GMT")
        self.assertEqual(parsed, SERVER)

    def test_garbage_is_none(self) -> None:
        self.assertIsNone(parse_http_date("not a date"))
        self.assertIsNone(parse_http_date(""))


class SkewTest(unittest.TestCase):
    def test_local_ahead_is_positive(self) -> None:
        skew = skew_seconds(SERVER, SERVER + timedelta(hours=4))
        self.assertAlmostEqual(skew, 4 * 3600.0)

    def test_local_behind_is_negative(self) -> None:
        skew = skew_seconds(SERVER, SERVER - timedelta(minutes=3))
        self.assertAlmostEqual(skew, -180.0)

    def test_naive_local_is_treated_as_utc(self) -> None:
        skew = skew_seconds(SERVER, datetime(2026, 9, 10, 10, 24, 25))
        self.assertAlmostEqual(skew, 5.0)


class BlocksEntriesTest(unittest.TestCase):
    def _status(self, skew, reachable=True, checked=True):
        from trader_core.clockcheck import ClockStatus

        return ClockStatus(
            reachable=reachable,
            skew_seconds=skew,
            checked_at=SERVER if checked else None,
        )

    def test_within_tolerance_is_fine(self) -> None:
        self.assertFalse(blocks_entries(self._status(30.0), 300.0))
        self.assertFalse(blocks_entries(self._status(-299.0), 300.0))

    def test_beyond_tolerance_blocks_both_directions(self) -> None:
        self.assertTrue(blocks_entries(self._status(4 * 3600.0), 300.0))
        self.assertTrue(blocks_entries(self._status(-4 * 3600.0), 300.0))

    def test_unreachable_is_warn_only_by_default(self) -> None:
        status = self._status(None, reachable=False, checked=False)
        self.assertFalse(blocks_entries(status, 300.0, fail_closed=False))
        self.assertTrue(blocks_entries(status, 300.0, fail_closed=True))


class ClockGuardTest(unittest.TestCase):
    def _guard(self, source=SERVER, fail_closed=False, cache_seconds=0.0, error=None):
        calls = {"n": 0}

        def fetch(_url, _timeout):
            calls["n"] += 1
            if error is not None:
                raise error
            return source

        guard = ClockGuard(
            url="https://clock.invalid",
            max_skew_seconds=300.0,
            cache_seconds=cache_seconds,
            fail_closed=fail_closed,
            fetch=fetch,
            now=lambda: SERVER,  # pretend the local clock equals server time
        )
        guard.calls = calls  # type: ignore[attr-defined]
        return guard

    def test_skew_blocks_entries(self) -> None:
        guard = ClockGuard(
            url="https://clock.invalid",
            max_skew_seconds=300.0,
            fetch=lambda _u, _t: SERVER,
            now=lambda: SERVER + timedelta(hours=4),
        )
        self.assertTrue(guard.blocks())
        self.assertIn("BLOCKED", guard.describe())

    def test_in_sync_does_not_block(self) -> None:
        guard = self._guard()
        self.assertFalse(guard.blocks())
        self.assertNotIn("BLOCKED", guard.describe())

    def test_unreachable_warns_and_does_not_block_by_default(self) -> None:
        guard = self._guard(error=OSError("no route to host"))
        self.assertFalse(guard.blocks())
        self.assertIn("UNVERIFIED", guard.describe())
        self.assertIn("no route", guard.describe())

    def test_unreachable_blocks_when_fail_closed(self) -> None:
        guard = self._guard(error=OSError("offline"), fail_closed=True)
        self.assertTrue(guard.blocks())

    def test_empty_url_disables_the_check(self) -> None:
        """An empty URL is "do not verify", never "block everything"."""
        guard = ClockGuard(url="", fail_closed=True, fetch=lambda _u, _t: SERVER)
        self.assertFalse(guard.blocks())
        self.assertIn("disabled", guard.describe())

    def test_result_is_cached(self) -> None:
        guard = self._guard(cache_seconds=600.0)
        guard.check()
        guard.check()
        guard.check()
        self.assertEqual(guard.calls["n"], 1)  # type: ignore[attr-defined]

    def test_zero_cache_rechecks_every_time(self) -> None:
        guard = self._guard(cache_seconds=0.0)
        guard.check()
        guard.check()
        self.assertEqual(guard.calls["n"], 2)  # type: ignore[attr-defined]

    def test_describe_reports_direction(self) -> None:
        guard = ClockGuard(
            url="https://clock.invalid",
            fetch=lambda _u, _t: SERVER,
            now=lambda: SERVER - timedelta(seconds=12),
        )
        self.assertIn("behind", guard.describe())


class FromConfigTest(unittest.TestCase):
    def test_reads_the_config_knobs(self) -> None:
        from trader_core.config import Config

        cfg = Config(
            clock_check_url="https://example.invalid",
            clock_max_skew_seconds=42.0,
            clock_check_interval_seconds=7.0,
            clock_check_fail_closed=True,
            clock_check_timeout_seconds=3.0,
        )
        guard = ClockGuard.from_config(cfg)
        self.assertEqual(guard.url, "https://example.invalid")
        self.assertAlmostEqual(guard.max_skew_seconds, 42.0)
        self.assertAlmostEqual(guard.cache_seconds, 7.0)
        self.assertTrue(guard.fail_closed)
        self.assertAlmostEqual(guard.timeout, 3.0)

    def test_defaults_are_conservative(self) -> None:
        from trader_core.config import Config

        cfg = Config()
        self.assertAlmostEqual(cfg.clock_max_skew_seconds, 300.0)
        self.assertFalse(cfg.clock_check_fail_closed)  # offline keeps trading


if __name__ == "__main__":
    unittest.main()
