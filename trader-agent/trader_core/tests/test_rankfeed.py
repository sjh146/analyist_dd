"""Tests for the self-generated candidate source (:mod:`trader_core.rankfeed`).

These tests pin the behaviour that matters operationally: the shortlist is
small (request budget), 상한가 is skipped by default, the filters actually
filter, and the tuned params reach the engine as Kelly priors.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from trader_core.bridge_client import BridgeResponseError
from trader_core.config import Config
from trader_core.feed import FeedError
from trader_core.rankfeed import (
    DEFAULT_PARAMS,
    DEFAULT_R1_MIN_AVG_SCORE,
    RankFeedClient,
    load_params,
    momentum_score,
    pct_change,
    percentile_ranks,
    percentile_scores,
    sign_char,
)


class FakeBridge:
    """Minimal bridge double exposing ``rank`` / ``quotes``."""

    def __init__(self, items, quotes, rank_error=None, quote_error=None) -> None:
        self.items = items
        self.quotes_map = quotes
        self.rank_error = rank_error
        self.quote_error = quote_error
        self.rank_calls = []
        self.quote_calls = []

    def rank(self, **kwargs):
        self.rank_calls.append(kwargs)
        if self.rank_error is not None:
            raise self.rank_error
        return {"items": list(self.items), "total_count": len(self.items), "pages": 1}

    def quotes(self, codes):
        self.quote_calls.append(list(codes))
        if self.quote_error is not None:
            raise self.quote_error
        return {code: self.quotes_map[code] for code in codes if code in self.quotes_map}


def _row(code, name, diff, sign, volume, rank):
    return {
        "code": code,
        "name": name,
        "diff_sign": ord(sign),
        "diff": diff,
        "volume": volume,
        "rank": rank,
    }


def _default_items():
    """A ranking page: one 상한가, three tradeable movers, one thin name."""
    return [
        _row("007540", "샘표", 12850, "1", 42736, 1),        # 상한가
        _row("214680", "디알텍", 1230, "2", 1_887_176, 4),
        _row("024060", "흥구석유", 2480, "2", 19_867_822, 5),
        _row("210980", "SK디앤디", 750, "2", 1_283_981, 6),
        _row("999999", "얇은종목", 300, "2", 10, 7),          # volume too thin
    ]


def _default_quotes():
    return {
        "007540": {"code": "007540", "name": "샘표", "current": 42850, "amount": 5000},
        "214680": {"code": "214680", "name": "디알텍", "current": 14680, "amount": 30000},
        "024060": {"code": "024060", "name": "흥구석유", "current": 16860, "amount": 900000},
        "210980": {"code": "210980", "name": "SK디앤디", "current": 14800, "amount": 120000},
        "999999": {"code": "999999", "name": "얇은종목", "current": 1200, "amount": 10},
    }


class PctChangeTest(unittest.TestCase):
    def test_rising_move(self) -> None:
        # 1,250 up on a 26,750 price -> previous close 25,500 -> +4.90 %
        value = pct_change(26750, 1250, 50)
        self.assertAlmostEqual(value, 1250 / 25500 * 100, places=6)

    def test_falling_move_is_negative(self) -> None:
        value = pct_change(26750, 1250, ord("5"))
        self.assertAlmostEqual(value, -1250 / 28000 * 100, places=6)

    def test_missing_price_is_none(self) -> None:
        self.assertIsNone(pct_change(None, 100, 50))
        self.assertIsNone(pct_change(100, None, 50))

    def test_sign_char_accepts_int_and_str(self) -> None:
        self.assertEqual(sign_char(49), "1")
        self.assertEqual(sign_char("2"), "2")
        self.assertEqual(sign_char(None), "")


class MomentumScoreTest(unittest.TestCase):
    def test_monotonic_in_pct_and_volume(self) -> None:
        weak = momentum_score(1.0, 200_000, 10000, 10, 60, False)
        strong = momentum_score(9.0, 5_000_000, 500000, 2, 60, False)
        self.assertLess(weak, strong)

    def test_limit_up_is_penalised(self) -> None:
        normal = momentum_score(9.0, 5_000_000, 500000, 2, 60, False)
        limit_up = momentum_score(9.0, 5_000_000, 500000, 2, 60, True)
        self.assertLess(limit_up, normal)

    def test_score_stays_within_0_100(self) -> None:
        extreme = momentum_score(500.0, 10**12, 10**9, 1, 60, False)
        self.assertLessEqual(extreme, 100.0)
        self.assertGreaterEqual(momentum_score(None, None, None, 60, 60, False), 0.0)


class PullbackFilterTest(unittest.TestCase):
    """Never buy within ``min_pullback_pct`` of the day's high."""

    def _client(self, quotes, **overrides):
        items = [
            _row("005930", "삼성전자", 1000, "2", 5_000_000, 1),
            _row("000660", "SK하이닉스", 1000, "2", 5_000_000, 2),
        ]
        params = dict(
            DEFAULT_PARAMS, min_pct=0.0, max_pct=0.0, min_volume=0,
            min_pullback_pct=3.0, scoring="absolute",
        )
        params.update(overrides)
        return RankFeedClient(
            FakeBridge(items, quotes), self.config, params=params
        )

    def setUp(self) -> None:
        self.config = Config(feed_source="rank")

    def _quote(self, current, high):
        return {"code": "x", "current": current, "high": high, "low": 1000,
                "amount": 100000}

    def test_near_the_high_is_skipped(self) -> None:
        quotes = {
            "005930": self._quote(20_000, 20_100),   # -0.5 % from high -> skip
            "000660": self._quote(20_000, 21_500),   # -7 % from high -> keep
        }
        snapshot = self._client(quotes).fetch()
        codes = [c.stock_code for c in snapshot.candidates["rank_momentum"]]
        self.assertEqual(codes, ["000660"])

    def test_disabled_keeps_both(self) -> None:
        quotes = {
            "005930": self._quote(20_000, 20_100),
            "000660": self._quote(20_000, 21_500),
        }
        snapshot = self._client(quotes, min_pullback_pct=0.0).fetch()
        codes = [c.stock_code for c in snapshot.candidates["rank_momentum"]]
        self.assertEqual(codes, ["005930", "000660"])

    def test_missing_high_fails_open(self) -> None:
        quotes = {
            "005930": {"code": "005930", "current": 20_000, "amount": 100000},
            "000660": {"code": "000660", "current": 20_000, "amount": 100000},
        }
        snapshot = self._client(quotes).fetch()
        self.assertEqual(len(snapshot.candidates["rank_momentum"]), 2)

    def test_day_range_reaches_the_engine_as_an_extra(self) -> None:
        quotes = {
            "005930": self._quote(20_000, 21_500),   # low 1,000 -> huge range
            "000660": self._quote(20_000, 21_500),
        }
        snapshot = self._client(quotes).fetch()
        candidate = snapshot.candidates["rank_momentum"][0]
        # (21,500 - 1,000) / 1,000 = 2,050 % -- an absurd range, which the
        # engine's max_stop_pct then clamps; the point is that it is carried.
        self.assertAlmostEqual(float(candidate.extras["day_range_pct"]), 2050.0)


class PercentileScoreTest(unittest.TestCase):
    """Within-snapshot percentiles: the fix for the 88-99 saturation."""

    def _metrics(self, rows):
        return [
            {"pct": pct, "volume": vol, "turnover": turn, "position": pos,
             "limit_up": limit_up}
            for pos, (pct, vol, turn, limit_up) in enumerate(rows, start=1)
        ]

    def test_spreads_across_the_whole_range(self) -> None:
        scores = percentile_scores(
            self._metrics(
                [(20.0, 5_000_000, 500_000, False),
                 (10.0, 1_000_000, 100_000, False),
                 (5.0, 100_000, 10_000, False)]
            )
        )
        self.assertGreater(scores[0], 90.0)   # best of the set
        self.assertLess(scores[-1], 10.0)     # worst of the set
        self.assertGreater(scores[0] - scores[-1], 80.0)  # real discrimination

    def test_monotonic_in_strength(self) -> None:
        scores = percentile_scores(
            self._metrics(
                [(30.0, 9_000_000, 900_000, False),
                 (15.0, 3_000_000, 300_000, False),
                 (3.0, 200_000, 20_000, False)]
            )
        )
        self.assertGreater(scores[0], scores[1])
        self.assertGreater(scores[1], scores[2])

    def test_limit_up_is_penalised(self) -> None:
        normal = percentile_scores(self._metrics([(20.0, 1_000_000, 100_000, False)]))
        limit_up = percentile_scores(self._metrics([(20.0, 1_000_000, 100_000, True)]))
        self.assertLess(limit_up[0], normal[0])

    def test_single_candidate_is_top_percentile(self) -> None:
        scores = percentile_scores(self._metrics([(10.0, 1_000_000, 100_000, False)]))
        self.assertGreater(scores[0], 90.0)

    def test_percentile_ranks_average_ties(self) -> None:
        self.assertEqual(percentile_ranks([1.0, 1.0, 1.0]), [0.5, 0.5, 0.5])
        self.assertEqual(percentile_ranks([5.0, 1.0]), [1.0, 0.0])
        self.assertEqual(percentile_ranks([7.0]), [1.0])
        self.assertEqual(percentile_ranks([]), [])

    def test_snapshot_scores_are_not_saturated(self) -> None:
        """The regression: candidates used to all land in 88-99."""
        quotes = {
            "007540": {"code": "007540", "current": 42850, "amount": 5000,
                       "high": 43000, "low": 30000},
            "214680": {"code": "214680", "current": 14680, "amount": 30000,
                       "high": 15000, "low": 13000},
            "024060": {"code": "024060", "current": 16860, "amount": 900000,
                       "high": 17000, "low": 14000},
            "210980": {"code": "210980", "current": 14800, "amount": 120000,
                       "high": 15100, "low": 14000},
        }
        client = RankFeedClient(
            FakeBridge(_default_items(), quotes),
            Config(feed_source="rank"),
            params=dict(DEFAULT_PARAMS, max_pct=0.0, min_pullback_pct=0.0),
        )
        scores = [
            c.score for c in client.fetch().candidates["rank_momentum"]
        ]
        self.assertGreaterEqual(len(scores), 3)
        self.assertLess(min(scores), 60.0)   # someone must be "weak"
        self.assertGreater(max(scores), 80.0)
        self.assertGreater(max(scores) - min(scores), 40.0)


class LoadParamsTest(unittest.TestCase):
    def test_r1_floor_is_below_the_structural_mean(self) -> None:
        """The R1 floor cannot judge a self-scored shortlist, so it must not block.

        Percentile scoring rates candidates inside their own snapshot: when the
        shortlist is the whole candidate set the top-N average is ~50 by
        construction (measured 2026-09-11: 84/58/50/44/40/24 -> 50.0).  A floor
        at or above 50 blocks every entry; a floor below it never fires.  The
        pct-based guards carry the "too hot" judgement for this feed instead.
        """
        self.assertLess(DEFAULT_R1_MIN_AVG_SCORE, 50.0)

    def test_missing_file_returns_defaults(self) -> None:
        self.assertEqual(load_params("/nonexistent/params.json"), dict(DEFAULT_PARAMS))

    def test_malformed_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "params.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            self.assertEqual(load_params(path), dict(DEFAULT_PARAMS))

    def test_partial_file_overrides_only_its_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "params.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"min_pct": 5.0, "prior_win_rate": 0.6}, handle)
            params = load_params(path)
            self.assertEqual(params["min_pct"], 5.0)
            self.assertEqual(params["prior_win_rate"], 0.6)
            self.assertEqual(params["top_n"], DEFAULT_PARAMS["top_n"])


class RankFeedClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = FakeBridge(_default_items(), _default_quotes())
        self.config = Config(feed_source="rank")

    def test_snapshot_carries_ranked_tradeable_candidates(self) -> None:
        snapshot = self.bridge and RankFeedClient(self.bridge, self.config).fetch()
        self.assertEqual(snapshot.source, "creon-rank")
        candidates = snapshot.candidates["rank_momentum"]
        codes = [c.stock_code for c in candidates]
        self.assertNotIn("007540", codes)  # 상한가 skipped by default
        self.assertNotIn("999999", codes)  # volume below min_volume
        self.assertNotIn("024060", codes)  # +17.2% -> over the chase ceiling
        self.assertEqual(codes, ["214680", "210980"])
        for candidate in candidates:
            self.assertIsNotNone(candidate.close_price)
            self.assertGreater(candidate.close_price, 0)
            self.assertGreater(candidate.score, 0)
            self.assertIn("위", candidate.reason)

    def test_limit_up_is_included_when_configured(self) -> None:
        # max_pct=0 isolates the 상한가 switch: the fixture's limit-up name is
        # also +42 %, so the chase ceiling would otherwise drop it too.
        params = dict(
            DEFAULT_PARAMS, skip_limit_up=False, min_volume=0, max_pct=0.0
        )
        snapshot = RankFeedClient(self.bridge, self.config, params=params).fetch()
        codes = [c.stock_code for c in snapshot.candidates["rank_momentum"]]
        self.assertIn("007540", codes)

    def test_shortlist_bounds_the_quote_requests(self) -> None:
        params = dict(DEFAULT_PARAMS, top_n=2)
        RankFeedClient(self.bridge, self.config, params=params).fetch()
        self.assertEqual(len(self.bridge.quote_calls), 1)
        self.assertEqual(len(self.bridge.quote_calls[0]), 2)

    def test_default_shortlist_samples_eight_rows(self) -> None:
        """Operational shortlist depth: 8 rows == 8 quote requests per cycle."""
        items = [
            _row("{0:06d}".format(310000 + i), "종목{0}".format(i), 1400, "2",
                 500_000, i + 1)
            for i in range(12)
        ]
        quotes = {
            row["code"]: {"code": row["code"], "current": 11400, "high": 11900,
                          "amount": 500_000}
            for row in items
        }
        bridge = FakeBridge(items, quotes)
        snapshot = RankFeedClient(bridge, self.config).fetch()
        self.assertEqual(DEFAULT_PARAMS["top_n"], 8)
        self.assertEqual(len(bridge.quote_calls[0]), 8)
        self.assertEqual(len(snapshot.candidates["rank_momentum"]), 8)

    def test_ceiling_admits_the_measured_band(self) -> None:
        """+14 % clears the 15 % ceiling, +16 % does not (probe 2026-09-11).

        The ceiling only means something together with the ranking window
        (``Config.rank_pct_to``): a 30 % window is sorted by 상승률 descending,
        so its head sits above every ceiling and the feed yields nothing.
        """
        items = [
            _row("111111", "밴드안", 1400, "2", 500_000, 1),   # +14.0 %
            _row("222222", "밴드밖", 1600, "2", 500_000, 2),   # +16.0 %
        ]
        quotes = {
            "111111": {"code": "111111", "current": 11400, "high": 11900},
            "222222": {"code": "222222", "current": 11600, "high": 11900},
        }
        snapshot = RankFeedClient(FakeBridge(items, quotes), self.config).fetch()
        codes = [c.stock_code for c in snapshot.candidates["rank_momentum"]]
        self.assertEqual(codes, ["111111"])

    def test_min_pct_filter_drops_weak_movers(self) -> None:
        params = dict(DEFAULT_PARAMS, min_pct=4.0)
        snapshot = RankFeedClient(self.bridge, self.config, params=params).fetch()
        codes = [c.stock_code for c in snapshot.candidates["rank_momentum"]]
        # 214680: +1230/13450 = 9.1 %, 210980: +750/14050 = 5.3 %
        # (024060 is +17.2 % -> dropped by the chase ceiling, not by min_pct)
        self.assertEqual(codes, ["214680", "210980"])
        # max_pct=0 isolates the floor: only the extended mover clears 15 %.
        params = dict(DEFAULT_PARAMS, min_pct=15.0, max_pct=0.0)
        snapshot = RankFeedClient(self.bridge, self.config, params=params).fetch()
        self.assertEqual(
            [c.stock_code for c in snapshot.candidates["rank_momentum"]], ["024060"]
        )

    def test_chase_ceiling_drops_extended_movers(self) -> None:
        """Past ``max_pct`` the entry buys someone else's profit-taking supply."""
        codes = [
            c.stock_code
            for c in RankFeedClient(self.bridge, self.config).fetch().candidates[
                "rank_momentum"
            ]
        ]
        self.assertNotIn("024060", codes)  # +17.2 % > default 12 %
        params = dict(DEFAULT_PARAMS, max_pct=20.0)
        codes = [
            c.stock_code
            for c in RankFeedClient(
                self.bridge, self.config, params=params
            ).fetch().candidates["rank_momentum"]
        ]
        self.assertIn("024060", codes)  # a looser ceiling lets it back in
        params = dict(DEFAULT_PARAMS, max_pct=0.0)
        codes = [
            c.stock_code
            for c in RankFeedClient(
                self.bridge, self.config, params=params
            ).fetch().candidates["rank_momentum"]
        ]
        self.assertIn("024060", codes)  # 0 disables the ceiling

    def test_empty_ranking_is_a_fresh_empty_snapshot(self) -> None:
        snapshot = RankFeedClient(FakeBridge([], {}), self.config).fetch()
        self.assertEqual(snapshot.candidates["rank_momentum"], [])
        self.assertTrue(snapshot.is_fresh(snapshot.generated_at, 60))
        self.assertEqual(snapshot.scoring_stats, {})

    def test_bridge_rank_failure_becomes_feed_error(self) -> None:
        bridge = FakeBridge([], {}, rank_error=BridgeResponseError("rate_limited", "no slot"))
        with self.assertRaises(FeedError) as cm:
            RankFeedClient(bridge, self.config).fetch()
        self.assertIn("rate_limited", str(cm.exception))

    def test_bridge_quote_failure_becomes_feed_error(self) -> None:
        bridge = FakeBridge(_default_items(), {}, quote_error=OSError("boom"))
        with self.assertRaises(FeedError):
            RankFeedClient(bridge, self.config).fetch()

    def test_journal_priors_reach_the_engine_via_scoring_stats(self) -> None:
        params = dict(
            DEFAULT_PARAMS,
            prior_win_rate=0.62,
            prior_avg_win_pct=3.5,
            prior_avg_loss_pct=1.8,
        )
        snapshot = RankFeedClient(self.bridge, self.config, params=params).fetch()
        stats = snapshot.stats_for("rank_momentum")
        self.assertEqual(stats["win_rate"], 62.0)  # fraction -> percent for the engine
        self.assertEqual(stats["avg_win_pct"], 3.5)
        self.assertEqual(stats["avg_loss_pct"], 1.8)

    def test_params_file_is_re_read_on_every_fetch(self) -> None:
        """A live tuning cycle must take effect without restarting the loop."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "params.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"top_n": 1}, handle)
            config = Config(feed_source="rank", rank_params_path=path)
            client = RankFeedClient(self.bridge, config)
            client.fetch()
            self.assertEqual(len(self.bridge.quote_calls[0]), 1)

            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"top_n": 3}, handle)
            client.fetch()
            self.assertEqual(len(self.bridge.quote_calls[1]), 3)

    def test_rank_request_uses_the_configured_window(self) -> None:
        config = Config(feed_source="rank", rank_limit=25, rank_pct_to=100, rank_sort=21)
        RankFeedClient(self.bridge, config).fetch()
        call = self.bridge.rank_calls[0]
        self.assertEqual(call["limit"], 25)
        self.assertEqual(call["pct_to"], 100)
        self.assertEqual(call["sort"], 21)


if __name__ == "__main__":
    unittest.main()
