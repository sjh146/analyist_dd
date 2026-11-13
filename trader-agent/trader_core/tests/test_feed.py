"""Feed parsing tests (real-shape fixture, empty stats, staleness, malformed)."""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone

from trader_core.feed import (
    Candidate,
    FeedError,
    FeedSnapshot,
    ScreenerFeedClient,
    candidate_change_pct,
    normalize_code,
    parse_timestamp,
)

from trader_core.tests import support

KST = timezone(timedelta(hours=9))


class FeedParseTest(unittest.TestCase):
    def test_real_shape_fixture_parses(self) -> None:
        snapshot = FeedSnapshot.from_text(support.read_feed_fixture())
        self.assertIsNotNone(snapshot.generated_at.tzinfo)
        self.assertEqual(snapshot.generated_at.utcoffset(), timedelta(hours=9))
        close = snapshot.candidates["close"]
        day = snapshot.candidates["daytrading"]
        self.assertEqual(len(close), 20)
        self.assertEqual(len(snapshot.candidates["swing"]), 0)
        self.assertEqual(len(day), 20)
        first = close[0]
        self.assertIsInstance(first, Candidate)
        self.assertEqual(first.stock_code, "443670")
        self.assertEqual(first.rank, 1)
        self.assertEqual(first.score, 95.0)
        self.assertEqual(first.close_price, 3780.0)
        # optional extras preserved verbatim (string-typed in the feed)
        self.assertEqual(first.extras["volume_surge"], "71.94")
        self.assertIn("close_strength", first.extras)
        dt = day[0]
        self.assertIn("kalman_trend", dt.extras)
        self.assertEqual(dt.stock_code, "047080")

    def test_empty_screener_stats_is_safe(self) -> None:
        snapshot = FeedSnapshot.from_text(support.read_feed_fixture())
        # real 2026-09-04 export has empty screener_stats {}
        self.assertEqual(snapshot.scoring_stats, {})
        for screener in ("close", "swing", "daytrading"):
            self.assertEqual(snapshot.stats_for(screener), {})

    def test_empty_string_numbers_become_none(self) -> None:
        item = support.make_candidate(close_price="", score="", rank="")
        item["short_ratio"] = ""
        snapshot = FeedSnapshot.from_payload(
            support.make_payload(close=[item])
        )
        cand = snapshot.candidates["close"][0]
        self.assertIsNone(cand.close_price)
        self.assertIsNone(cand.score)
        self.assertIsNone(cand.rank)

    def test_stale_generated_at_is_not_fresh(self) -> None:
        generated = datetime(2026, 9, 4, 10, 0, tzinfo=KST)
        snapshot = FeedSnapshot.from_payload(
            support.make_payload(generated_at=generated.isoformat())
        )
        self.assertTrue(snapshot.is_fresh(generated + timedelta(hours=1), 6 * 3600))
        self.assertFalse(snapshot.is_fresh(generated + timedelta(hours=7), 6 * 3600))
        # boundary: exactly max_age is still fresh
        self.assertTrue(snapshot.is_fresh(generated + timedelta(hours=6), 6 * 3600))

    def test_naive_timestamp_assumed_kst(self) -> None:
        snapshot = FeedSnapshot.from_payload(
            support.make_payload(generated_at="2026-09-04T10:00:00")
        )
        self.assertEqual(snapshot.generated_at.utcoffset(), timedelta(hours=9))

    def test_malformed_payload_raises(self) -> None:
        with self.assertRaises(FeedError):
            FeedSnapshot.from_payload({})
        with self.assertRaises(FeedError):
            FeedSnapshot.from_payload({"candidates": []})
        with self.assertRaises(FeedError):
            FeedSnapshot.from_text("{not json")
        with self.assertRaises(FeedError):
            FeedSnapshot.from_text(json.dumps({"a": 1}))

    def test_non_six_digit_code_dropped(self) -> None:
        good = support.make_candidate(code="005930", rank=1)
        bad_prefix = support.make_candidate(code="US005930", rank=2)
        bad_short = support.make_candidate(code="123", rank=3)
        prefixed = support.make_candidate(code="A000660", rank=4)
        snapshot = FeedSnapshot.from_payload(
            support.make_payload(close=[good, bad_prefix, bad_short, prefixed])
        )
        codes = [c.stock_code for c in snapshot.candidates["close"]]
        self.assertEqual(codes, ["005930", "000660"])

    def test_normalize_code(self) -> None:
        self.assertEqual(normalize_code("005930"), "005930")
        self.assertEqual(normalize_code("A005930"), "005930")
        self.assertIsNone(normalize_code("US005930"))
        self.assertIsNone(normalize_code(""))

    def test_parse_timestamp_error(self) -> None:
        with self.assertRaises(FeedError):
            parse_timestamp("not-a-time")
        with self.assertRaises(FeedError):
            parse_timestamp(None)

    def test_fetch_file_local(self) -> None:
        client = ScreenerFeedClient("/nonexistent/path.json")
        snapshot = client.fetch_file(support.FEED_FIXTURE)
        self.assertEqual(len(snapshot.candidates["close"]), 20)
        with self.assertRaises(FeedError):
            client.fetch_file("/nonexistent/feed.json")

    def test_fetch_local_path_auto_detected(self) -> None:
        client = ScreenerFeedClient(support.FEED_FIXTURE)
        snapshot = client.fetch()
        self.assertEqual(len(snapshot.candidates["close"]), 20)


class CandidateChangePctTest(unittest.TestCase):
    """Both producers' field names must resolve to the same number.

    ``pct_change`` (rank feed) and ``day_change_pct`` (screener feed) land in
    ``extras`` verbatim, so a single-key reader silently loses one feed's value.
    """

    @staticmethod
    def _pct(**extras):
        candidate = Candidate(rank=1, stock_code="005930", extras=dict(extras))
        return candidate_change_pct(candidate)

    def _assert_pct(self, extras, expected) -> None:
        value = self._pct(**extras)
        assert value is not None
        self.assertAlmostEqual(value, expected)

    def test_rank_feed_key(self) -> None:
        self._assert_pct({"pct_change": "7.25"}, 7.25)

    def test_screener_feed_key(self) -> None:
        self._assert_pct({"day_change_pct": 29.99}, 29.99)

    def test_rank_feed_key_wins_when_both_present(self) -> None:
        self._assert_pct({"pct_change": "3.5", "day_change_pct": 11.0}, 3.5)

    def test_falls_through_unparseable_first_key(self) -> None:
        self._assert_pct({"pct_change": "", "day_change_pct": "12.5"}, 12.5)

    def test_missing_key_is_none(self) -> None:
        self.assertIsNone(self._pct())
        self.assertIsNone(self._pct(volume=1234))


if __name__ == "__main__":
    unittest.main()
