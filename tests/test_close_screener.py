"""close_screener / close_screener_performance DB-free unit tests."""
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import close_screener
import close_screener_performance


def _make_series(close_prices, volumes, highs=None, lows=None, opens=None,
                 trading_values=None, code="000001", start=date(2026, 8, 10)):
    """7행 단일 종목 시계열 DataFrame 생성."""
    n = len(close_prices)
    if highs is None:
        highs = [c * 1.02 for c in close_prices]
    if lows is None:
        lows = [c * 0.98 for c in close_prices]
    if opens is None:
        opens = close_prices
    if trading_values is None:
        trading_values = [1e9] * n
    dates = [date.fromordinal(start.toordinal() + i) for i in range(n)]
    return pd.DataFrame({
        "stock_code": [code] * n,
        "trade_date": dates,
        "open_price": [float(x) for x in opens],
        "high_price": [float(x) for x in highs],
        "low_price": [float(x) for x in lows],
        "close_price": [float(x) for x in close_prices],
        "volume": [float(x) for x in volumes],
        "trading_value": [float(x) for x in trading_values],
    })


# ── 1. compute_indicators ──────────────────────────────────────────────
class TestComputeIndicators:
    def test_hand_computed_single_stock(self):
        # 7행: r1..r6 (r6=당일). close: 100,102,101,105,104,110,120
        closes = [100, 102, 101, 105, 104, 110, 120]
        vols = [1000, 1000, 1000, 1000, 1000, 1000, 4000]
        df = _make_series(closes, vols)
        out = close_screener.compute_indicators(df, lookback=7)
        assert len(out) == 1
        r = out.iloc[0]
        # volume_surge = v6 / mean(v1..v5) = 4000 / 1000 = 4.0
        assert r["volume_surge"] == pytest.approx(4.0)
        # close_strength = (c6-l6)/(h6-l6) = (120-117.6)/(122.4-117.6) = 0.5
        assert r["close_strength"] == pytest.approx(0.5)
        # closes=[r0..r6]=[100,102,101,105,104,110,120], r6=당일(120)
        # c1=r1=102(5거래일 전), c3=r3=105(3거래일 전), c5=r5=110(전일)
        # ret_3d = c6/c3 - 1 = 120/105 - 1
        assert r["ret_3d"] == pytest.approx(120 / 105 - 1)
        # ret_5d = c6/c1 - 1 = 120/102 - 1
        assert r["ret_5d"] == pytest.approx(120 / 102 - 1)
        # day_change = c6/c5 - 1 = 120/110 - 1
        assert r["day_change"] == pytest.approx(120 / 110 - 1)

    def test_high_equals_low(self):
        closes = [100, 102, 101, 105, 104, 110, 120]
        vols = [1000] * 7
        highs = [c * 1.0 for c in closes]  # h == l
        lows = [c * 1.0 for c in closes]
        df = _make_series(closes, vols, highs=highs, lows=lows)
        out = close_screener.compute_indicators(df, lookback=7)
        assert out.iloc[0]["close_strength"] == pytest.approx(0.5)

    def test_insufficient_rows_dropped(self):
        df = _make_series([100, 102, 101], [1000] * 3)
        out = close_screener.compute_indicators(df, lookback=7)
        assert len(out) == 0

    def test_multi_stock_one_row_each(self):
        df1 = _make_series([100, 102, 101, 105, 104, 110, 120], [1000] * 7, code="000001")
        df2 = _make_series([50, 52, 51, 55, 54, 60, 70], [2000] * 7, code="000002")
        df = pd.concat([df1, df2], ignore_index=True)
        out = close_screener.compute_indicators(df, lookback=7)
        assert len(out) == 2
        assert set(out["stock_code"]) == {"000001", "000002"}


# ── 2. score_candidates ────────────────────────────────────────────────
class TestScoreCandidates:
    def _base_df(self, **overrides):
        data = {
            "stock_code": ["000001"],
            "trade_date": [date(2026, 8, 19)],
            "open_price": [100.0], "high_price": [120.0], "low_price": [90.0],
            "close_price": [110.0], "volume": [4000.0], "trading_value": [1e9],
            "volume_surge": [4.0], "close_strength": [0.8],
            "ret_3d": [0.05], "ret_5d": [0.08], "day_change": [0.03],
            "short_ratio": [0.01],
        }
        data.update(overrides)
        return pd.DataFrame(data)

    def test_scores_within_bounds(self):
        df = self._base_df()
        out = close_screener.score_candidates(df)
        assert 0 <= out.iloc[0]["score"] <= 100

    def test_monotonicity_volume_surge(self):
        low = close_screener.score_candidates(self._base_df(volume_surge=1.0))
        high = close_screener.score_candidates(self._base_df(volume_surge=4.0))
        assert high.iloc[0]["score"] > low.iloc[0]["score"]

    def test_nan_short_ratio_scores_lower_than_zero(self):
        nan_df = self._base_df(short_ratio=np.nan)
        zero_df = self._base_df(short_ratio=0.0)
        nan_score = close_screener.score_candidates(nan_df).iloc[0]["score"]
        zero_score = close_screener.score_candidates(zero_df).iloc[0]["score"]
        # NaN → 중립 10점, 0.0 → 20점
        assert zero_score - nan_score == pytest.approx(10.0)
        assert nan_score < zero_score

    def test_regime_positive_adds_up_to_5(self):
        base = close_screener.score_candidates(self._base_df(), regime=None).iloc[0]["score"]
        pos = close_screener.score_candidates(
            self._base_df(), regime={"investor_net": 1e9, "program_net": 0}
        ).iloc[0]["score"]
        assert pos - base == pytest.approx(5.0)

    def test_regime_negative_subtracts_5(self):
        base = close_screener.score_candidates(self._base_df(), regime=None).iloc[0]["score"]
        neg = close_screener.score_candidates(
            self._base_df(), regime={"investor_net": -1e9, "program_net": 0}
        ).iloc[0]["score"]
        assert base - neg == pytest.approx(5.0)

    def test_reason_contains_georyang(self):
        out = close_screener.score_candidates(self._base_df())
        reason = out.iloc[0]["reason"]
        assert isinstance(reason, str) and len(reason) > 0
        assert "거래량" in reason

    def test_preserves_meta_columns(self):
        # 실DB 실행에서 stock_name/sector가 점수 계산 후 유실됐던 회귀 방지
        df = self._base_df()
        df["stock_name"] = ["화신정공"]
        df["sector"] = ["Unknown"]
        out = close_screener.score_candidates(df)
        assert out.iloc[0]["stock_name"] == "화신정공"
        assert out.iloc[0]["sector"] == "Unknown"


# ── 3. filter_candidates ───────────────────────────────────────────────
class TestFilterCandidates:
    def _df(self):
        return pd.DataFrame({
            "stock_code": ["a", "b", "c", "d", "e"],
            "trade_date": [date(2026, 8, 19)] * 5,
            "close_price": [5000.0, 500.0, 5000.0, 5000.0, 5000.0],
            "volume": [1000.0, 1000.0, 0.0, 1000.0, 1000.0],
            "trading_value": [5e8, 5e8, 5e8, 1e8, 5e8],
            "volume_surge": [2.0, 2.0, 2.0, 2.0, np.nan],
            "close_strength": [0.8, 0.8, 0.8, 0.8, 0.8],
            "ret_3d": [0.05, 0.05, 0.05, 0.05, 0.05],
            "ret_5d": [0.08, 0.08, 0.08, 0.08, 0.08],
            "day_change": [0.03, 0.03, 0.03, 0.03, 0.03],
        })

    def test_excludes_low_value_low_price_zero_vol_nan(self):
        out = close_screener.filter_candidates(self._df())
        # a만 통과 (b: 저가, c: 거래량0, d: 거래대금 부족, e: NaN 지표)
        assert list(out["stock_code"]) == ["a"]

    def test_trading_value_null_falls_back_to_close_times_volume(self):
        # 최근 데이터는 trading_value가 NULL → close×volume 근사로 판정
        df = self._df()
        df.loc[df.index[0], "trading_value"] = np.nan   # a: 근사 5000*1000=5e6 < 3e8 → 탈락
        df.loc[df.index[1], "trading_value"] = np.nan   # b: 저가로 이미 탈락
        out = close_screener.filter_candidates(df)
        assert list(out["stock_code"]) == []

        df2 = self._df()
        df2.loc[df2.index[0], "trading_value"] = np.nan
        df2.loc[df2.index[0], "close_price"] = 50000.0  # 근사 50000*1000=5e7 → 여전히 미달
        df2.loc[df2.index[0], "volume"] = 10000.0       # 근사 5e8 ≥ 3e8 → 통과
        out2 = close_screener.filter_candidates(df2)
        assert list(out2["stock_code"]) == ["a"]


# ── 4. rank_candidates ─────────────────────────────────────────────────
class TestRankCandidates:
    def test_ordering_rank_truncation(self):
        df = pd.DataFrame({
            "stock_code": [f"s{i}" for i in range(5)],
            "score": [10.0, 90.0, 50.0, 70.0, 30.0],
        })
        out = close_screener.rank_candidates(df, top_n=3)
        assert list(out["score"]) == [90.0, 70.0, 50.0]
        assert list(out["rank"]) == [1, 2, 3]
        assert len(out) == 3

    def test_empty_df_no_crash(self):
        # 실DB에서 필터 통과 0종목일 때 KeyError('score') 발생했던 회귀 방지
        empty = pd.DataFrame(columns=["stock_code", "score"])
        out = close_screener.rank_candidates(empty, top_n=20)
        assert len(out) == 0
        assert "rank" in out.columns

    def test_score_empty_df_returns_typed_frame(self):
        empty = pd.DataFrame(columns=["stock_code", "volume_surge", "close_strength",
                                      "ret_3d", "ret_5d", "day_change"])
        out = close_screener.score_candidates(empty)
        assert len(out) == 0
        assert "score" in out.columns
        assert "reason" in out.columns


# ── 5. build_output_rows + write_csv round-trip ────────────────────────
class TestOutput:
    def _ranked_df(self):
        return pd.DataFrame({
            "rank": [1],
            "stock_code": ["000001"],
            "stock_name": ["테스트"],
            "sector": ["IT"],
            "trade_date": [date(2026, 8, 19)],
            "close_price": [110.0],
            "score": [85.123],
            "volume_surge": [4.123],
            "close_strength": [0.8123],
            "ret_3d": [0.05123],
            "ret_5d": [0.08123],
            "day_change": [0.03123],
            "short_ratio": [0.01234],
            "reason": ["거래량 4.1배"],
        })

    def test_build_output_rows(self):
        rows = close_screener.build_output_rows(self._ranked_df())
        assert len(rows) == 1
        r = rows[0]
        assert list(r.keys()) == close_screener.OUTPUT_COLUMNS
        assert r["signal_date"] == "2026-08-19"
        assert r["score"] == pytest.approx(85.1)
        assert r["volume_surge"] == pytest.approx(4.12)
        assert r["close_strength"] == pytest.approx(0.812)
        assert r["ret_3d_pct"] == pytest.approx(5.12)
        assert r["ret_5d_pct"] == pytest.approx(8.12)
        assert r["day_change_pct"] == pytest.approx(3.12)
        assert r["short_ratio"] == "0.012"
        assert r["close_price"] == pytest.approx(110.0)

    def test_missing_short_ratio_empty_string(self):
        df = self._ranked_df()
        df["short_ratio"] = np.nan
        rows = close_screener.build_output_rows(df)
        assert rows[0]["short_ratio"] == ""

    def test_write_csv_roundtrip(self, tmp_path):
        rows = close_screener.build_output_rows(self._ranked_df())
        path = tmp_path / "sub" / "out.csv"
        close_screener.write_csv(rows, str(path))
        assert path.exists()
        df = pd.read_csv(path)
        assert list(df.columns) == close_screener.OUTPUT_COLUMNS
        assert df.iloc[0]["score"] == pytest.approx(85.1)


# ── 6. resolve_trade_date ──────────────────────────────────────────────
class TestResolveTradeDate:
    def test_none_case(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value
        cur.fetchone.return_value = (date(2026, 8, 19),)
        result = close_screener.resolve_trade_date(conn, None)
        assert result == "2026-08-19"

    def test_explicit_date_case(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value
        cur.fetchone.return_value = (date(2026, 8, 19),)
        result = close_screener.resolve_trade_date(conn, "2026-08-20")
        assert result == "2026-08-19"


# ── 7. parse_signal_date_from_filename ─────────────────────────────────
class TestParseFilename:
    def test_valid(self):
        d = close_screener.parse_signal_date_from_filename(
            "close_candidates_20260821_153000.csv"
        )
        assert d == date(2026, 8, 21)

    def test_invalid(self):
        with pytest.raises(ValueError):
            close_screener.parse_signal_date_from_filename("garbage.csv")


# ── 8. compute_trade_returns ───────────────────────────────────────────
class TestComputeTradeReturns:
    def test_known_numbers(self):
        open_ret, close_ret = close_screener_performance.compute_trade_returns(
            1000, 1010, 1030
        )
        assert open_ret == pytest.approx(1.0)
        assert close_ret == pytest.approx(3.0)

    def test_none_signal_close_raises(self):
        with pytest.raises(ValueError):
            close_screener_performance.compute_trade_returns(None, 1010, 1030)


# ── 9. summarize_results ───────────────────────────────────────────────
class TestSummarizeResults:
    def test_crafted_rows(self):
        rows = [
            {"open_sell_return_pct": 1.0, "close_sell_return_pct": 2.0},
            {"open_sell_return_pct": -1.0, "close_sell_return_pct": 4.0},
            {"open_sell_return_pct": 3.0, "close_sell_return_pct": -2.0},
            {"open_sell_return_pct": 5.0, "close_sell_return_pct": 0.0},
        ]
        out = close_screener_performance.summarize_results(rows)
        assert out["count"] == 4
        # open: [1,-1,3,5] → wins=3, win_rate=75, avg=2.0, median=2.0, max=5, min=-1
        assert out["open_sell"]["win_rate"] == pytest.approx(75.0)
        assert out["open_sell"]["avg_return_pct"] == pytest.approx(2.0)
        assert out["open_sell"]["median_return_pct"] == pytest.approx(2.0)
        assert out["open_sell"]["max_return_pct"] == pytest.approx(5.0)
        assert out["open_sell"]["min_return_pct"] == pytest.approx(-1.0)
        # close: [2,4,-2,0] → wins=2, win_rate=50, avg=1.0, median=1.0
        assert out["close_sell"]["win_rate"] == pytest.approx(50.0)
        assert out["close_sell"]["avg_return_pct"] == pytest.approx(1.0)
        assert out["close_sell"]["median_return_pct"] == pytest.approx(1.0)

    def test_empty(self):
        out = close_screener_performance.summarize_results([])
        assert out["count"] == 0
        assert out["open_sell"]["win_rate"] == 0.0
        assert out["close_sell"]["avg_return_pct"] == 0.0


# ── 10. load_candidate_files ───────────────────────────────────────────
class TestLoadCandidateFiles:
    def test_loads_and_skips_bad(self, tmp_path):
        # 정상 파일 (signal_date 컬럼 포함)
        good_rows = [{
            "rank": 1, "stock_code": "000001", "stock_name": "A", "sector": "IT",
            "signal_date": "2026-08-19", "close_price": 110.0, "score": 85.0,
            "volume_surge": 4.0, "close_strength": 0.8, "ret_3d_pct": 5.0,
            "ret_5d_pct": 8.0, "day_change_pct": 3.0, "short_ratio": "0.01",
            "reason": "거래량 4.0배",
        }]
        close_screener.write_csv(good_rows, str(tmp_path / "close_candidates_20260819_090000.csv"))

        # signal_date 없는 파일 (파일명에서 파싱)
        no_sig = [dict(r, signal_date="") for r in good_rows]
        close_screener.write_csv(no_sig, str(tmp_path / "close_candidates_20260818_090000.csv"))

        # 잘못된 파일 (stock_code 없음) → 스킵
        bad = pd.DataFrame({"foo": [1, 2]}).to_csv(str(tmp_path / "close_candidates_20260817_090000.csv"), index=False)

        files = close_screener_performance.load_candidate_files(str(tmp_path))
        assert len(files) == 2
        dates = sorted(d for d, _, _ in files)
        assert dates == [date(2026, 8, 18), date(2026, 8, 19)]
