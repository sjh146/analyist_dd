"""screener_score / daytrading_performance DB-free unit tests.

Covers:
 1. window calc: close (D→D+1 open), swing (D+1 close → D+7 close, off-by-one),
    daytrading (D close → D+1 open gap proxy).
 2. gap win/loss determination.
 3. dedup: same candidate run twice scores once (registry count).
 4. summary aggregation (win_rate/avg/sample_count).
 5. empty-input safety (no candidates / no registry → no crash).
 6. minute-provider abstraction & DailyGapProvider proxy.
"""
import os
import sys
from datetime import date, timedelta
from unittest import mock

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import screener_score
import daytrading_performance
from day_trading_engine import (DEFAULT_WINDOW_MINUTES, DailyGapProvider,
                                MinutePriceProvider)


# ── fake DB helper ────────────────────────────────────────────────────
class FakeCursor:
    """Minimal cursor returning pre-baked market_data rows (d, open, close)."""

    def __init__(self, rows):
        self.rows = list(rows)

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return [tuple(r) for r in self.rows]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class FakeConn:
    """pg-like object: cursor() yields a FakeCursor for the given rows."""

    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)

    def close(self):
        pass


def _d(signal_date, offset):
    return signal_date + timedelta(days=offset)


# ── 1. window calc ────────────────────────────────────────────────────
AD = date(2026, 8, 19)


class TestWindowCalc:
    def test_close_d_to_d1_open(self):
        # D=08-19 close 1000, D+1 open 1030 → +3.0%, win
        rows = [(AD, 1000.0, 1000.0), (_d(AD, 1), 1030.0, 1020.0)]
        pg = FakeConn(rows)
        out = screener_score.score_close(pg, "000001", AD)
        assert out["return_pct"] == pytest.approx(3.0)
        assert out["win"] is True
        assert out["sell_date"] == str(_d(AD, 1))

    def test_close_gap_down_is_loss(self):
        rows = [(AD, 1000.0, 1000.0), (_d(AD, 1), 980.0, 990.0)]
        out = screener_score.score_close(FakeConn(rows), "000001", AD)
        assert out["return_pct"] == pytest.approx(-2.0)
        assert out["win"] is False

    def test_close_missing_d1_returns_none(self):
        rows = [(AD, 1000.0, 1000.0)]
        assert screener_score.score_close(FakeConn(rows), "000001", AD) is None

    def test_swing_d1_to_d7_off_by_one(self):
        # D close=100. Base = D+1 close=100 (bars[1]). Sell = D+7 close=107
        # (bars[7]). return = 107/100 - 1 = +7%. Need 8 bars (D + D+1..D+7).
        rows = [(AD, 100.0, 100.0)]
        for i in range(1, 8):
            rows.append((_d(AD, i), 100.0, 100.0 if i < 7 else 107.0))
        out = screener_score.score_swing(FakeConn(rows), "000001", AD, horizon=7)
        assert out["sell_index"] == 7
        assert out["return_pct"] == pytest.approx(7.0)
        assert out["win"] is True
        assert out["sell_date"] == str(_d(AD, 7))

    def test_swing_insufficient_bars_returns_none(self):
        # only D + D+1..D+5 (7 bars) < needed 8 → None
        rows = [(AD, 100.0, 100.0)]
        for i in range(1, 6):
            rows.append((_d(AD, i), 100.0 + i, 100.0 + i))
        assert screener_score.score_swing(FakeConn(rows), "000001", AD, horizon=7) is None

    def test_daytrading_gap_proxy(self):
        # D close 1000 → D+1 open 1012 → +1.2% win
        rows = [(AD, 1000.0, 1000.0), (_d(AD, 1), 1012.0, 1005.0)]
        out = screener_score.score_daytrading(FakeConn(rows), "000001", AD)
        assert out["return_pct"] == pytest.approx(1.2)
        assert out["win"] is True


# ── 2. registry dedup ─────────────────────────────────────────────────
class TestRegistryDedup:
    def _candidates(self):
        # one daytrading candidate for registry testing
        return [("daytrading", AD, "000001",
                 {"stock_name": "A", "sector": "IT"}, "x.csv")]

    def _pg(self):
        rows = [(AD, 1000.0, 1000.0), (_d(AD, 1), 1010.0, 1005.0)]
        return FakeConn(rows)

    def test_same_candidate_scores_once(self, tmp_path):
        scoring_dir = str(tmp_path / "scoring")
        os.makedirs(scoring_dir, exist_ok=True)
        pg = self._pg()
        # First run: empty registry → 1 scored
        results, scored, skipped = screener_score.score_candidates(
            pg, self._candidates(), {}, scoring_dir)
        assert scored == 1 and skipped == 0 and len(results) == 1

        # Reload registry from disk (simulates fresh process) → now present
        registry = screener_score.load_registry(scoring_dir)
        assert screener_score.registry_key("daytrading", AD, "000001") in registry

        # Second run: same candidate → 0 scored, 1 skipped, no new file append
        results2, scored2, skipped2 = screener_score.score_candidates(
            self._pg(), self._candidates(), registry, scoring_dir)
        assert scored2 == 0 and skipped2 == 1 and results2 == []

        # scored.jsonl must contain exactly one record
        path = os.path.join(scoring_dir, "scored.jsonl")
        with open(path, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 1

    def test_registry_key_format(self):
        assert screener_score.registry_key("close", AD, "000001") == \
            f"close|{AD}|000001"


# ── 3. summary aggregation ────────────────────────────────────────────
class TestSummary:
    def test_aggregates_per_screener(self):
        results = [
            {"screener": "close", "return_pct": 3.0},
            {"screener": "close", "return_pct": -1.0},
            {"screener": "close", "return_pct": 2.0},
            {"screener": "swing", "return_pct": 5.0},
            {"screener": "swing", "return_pct": -2.0},
        ]
        stats = screener_score.summarize_per_screener(results)
        c = stats["close"]
        s = stats["swing"]
        assert c["sample_count"] == 3
        assert c["win_rate"] == pytest.approx(200 / 3, abs=0.1)
        assert c["avg_return_pct"] == pytest.approx(4 / 3, abs=0.01)
        assert s["sample_count"] == 2
        assert s["win_rate"] == 50.0

    def test_empty_results_no_crash(self):
        stats = screener_score.summarize_per_screener([])
        assert stats == {}


# ── 4. candidate scan + empty safety ──────────────────────────────────
class TestScanCandidates:
    def _write(self, path, rows, dtype_str=False):
        df = pd.DataFrame(rows, dtype=str if dtype_str else None)
        df.to_csv(path, index=False)

    def test_scan_parses_signal_date_from_column_and_filename(self, tmp_path):
        # daytrading: signal_date in column (6-digit code, keeps leading zero)
        self._write(tmp_path / "daytrading_candidates_20260820_090000.csv",
                    [{"stock_code": "000001", "signal_date": "2026-08-19",
                      "stock_name": "A"}],
                    dtype_str=True)
        # swing: no signal_date in column → from filename
        self._write(tmp_path / "swing_candidates_20260818_090000.csv",
                    [{"stock_code": "000002", "stock_name": "B"}],
                    dtype_str=True)
        cands = screener_score.scan_candidates(str(tmp_path),
                                               ["daytrading", "swing"])
        by = {c[2]: c for c in cands}
        assert by["000001"][1] == date(2026, 8, 19)
        assert by["000002"][1] == date(2026, 8, 18)

    def test_scan_empty_dir_returns_empty(self, tmp_path):
        assert screener_score.scan_candidates(str(tmp_path), ["close"]) == []

    def test_scan_dedups_same_code_in_file(self, tmp_path):
        rows = [{"stock_code": "000001", "signal_date": "2026-08-19"},
                {"stock_code": "000001", "signal_date": "2026-08-19"}]
        self._write(tmp_path / "close_candidates_20260819_090000.csv", rows,
                    dtype_str=True)
        cands = screener_score.scan_candidates(str(tmp_path), ["close"])
        assert len(cands) == 1


# ── 5. minute-provider abstraction ────────────────────────────────────
class TestMinuteProvider:
    def test_protocol_is_abstract(self):
        assert issubclass(DailyGapProvider, MinutePriceProvider)
        assert callable(getattr(MinutePriceProvider, "get_minute_price"))

    def test_default_window_30(self):
        assert DEFAULT_WINDOW_MINUTES == 30

    def test_daily_gap_proxy_uses_d1_open(self):
        # injected dict {(code,date): open} — no DB
        rows = {( "000001", str(_d(AD, 1))): 1010.0}
        prov = DailyGapProvider(rows)
        price = prov.get_minute_price("000001", _d(AD, 1))
        assert price == pytest.approx(1010.0)

    def test_daily_gap_proxy_none_when_missing(self):
        prov = DailyGapProvider({})
        assert prov.get_minute_price("000001", _d(AD, 1)) is None

    def test_daytrading_performance_score_gap_helpers(self):
        # gap +1.2% win
        ret, hit = daytrading_performance.score_gap(1000.0, 1012.0)
        assert ret == pytest.approx(1.2)
        assert hit is True
        # gap down loss
        ret, hit = daytrading_performance.score_gap(1000.0, 980.0)
        assert ret == pytest.approx(-2.0)
        assert hit is False
        # invalid base
        assert daytrading_performance.score_gap(None, 1012.0) == (None, None)
