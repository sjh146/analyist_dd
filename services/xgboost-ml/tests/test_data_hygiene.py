"""extra_experiments.py 의 순수 데이터 위생/라벨 함수 검증 (합성 데이터, DB/모델 불필요).

검증 대상:
  (a) 거래량 0 탐지
  (b) 직전 거래일과 OHLC 완전 동일(동결) 탐지
  (c) 위생 필터(제외 행 집계)
  (d) 시장상대(cross-sectional demean) 라벨 계산
  (e) 최근 가중 표본 복제
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

_CANDIDATES = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")),
]
for _c in _CANDIDATES:
    if os.path.exists(os.path.join(_c, "extra_experiments.py")):
        sys.path.insert(0, _c)
        break

import extra_experiments as ee


def _ohlc_df():
    return pd.DataFrame({
        "stock_code": ["A", "A", "A", "A"],
        "trade_date": ["2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"],
        "open_price": [100.0, 100.0, 105.0, 105.0],
        "high_price": [110.0, 110.0, 115.0, 115.0],
        "low_price": [90.0, 90.0, 100.0, 100.0],
        "close_price": [105.0, 105.0, 110.0, 110.0],
        "volume": [1000, 0, 500, 700],
    })


def test_detect_zero_volume():
    df = pd.DataFrame({"volume": [100, 0, 50, None]})
    mask = ee.detect_zero_volume(df, "volume")
    assert mask.tolist() == [False, True, False, True]


def test_detect_frozen_ohlc_marks_consecutive_duplicates():
    df = _ohlc_df()
    mask = ee.detect_frozen_ohlc(df)
    # d1 첫 행 유지, d2(==d1) 동결, d3 유지, d4(==d3) 동결
    assert mask.tolist() == [False, True, False, True]


def test_detect_frozen_ohlc_ignores_different_stocks():
    df = _ohlc_df()
    df.loc[3, "stock_code"] = "B"  # d4 를 다른 종목으로 -> 같은 종목 내 비교가 아니므로 동결 아님
    mask = ee.detect_frozen_ohlc(df)
    assert mask.tolist() == [False, True, False, False]


def test_hygiene_filter_counts():
    df = _ohlc_df()
    clean, stats = ee.hygiene_filter(df)
    # 제외: d2(vol0+동결), d4(동결) -> 2행 제외, vol0 1건, frozen 2건
    assert stats["n_before"] == 4
    assert stats["n_after"] == 2
    assert stats["n_zero_volume"] == 1
    assert stats["n_frozen"] == 2
    assert stats["n_dropped"] == 2
    assert clean["trade_date"].tolist() == ["2026-09-20", "2026-09-22"]


def test_market_relative_labels_cross_sectional_demean():
    df = pd.DataFrame({
        "stock_code": ["A", "A", "A", "B", "B", "B"],
        "date": ["d1", "d2", "d3", "d1", "d2", "d3"],
        "price": [100.0, 110.0, 121.0, 100.0, 90.0, 81.0],
    })
    labels = ee.market_relative_labels(df)
    # A: 다음날 수익률 d1=+10%, d2=+10%, d3=NaN->0
    # B: d1=-10%, d2=-10%, d3=NaN->0
    # 각 날짜 중앙값: d1=0, d2=0 -> A=1, B=0
    assert labels.tolist() == [1, 1, 0, 0, 0, 0]


def test_market_relative_labels_horizon2():
    df = pd.DataFrame({
        "stock_code": ["A", "A", "A"],
        "date": ["d1", "d2", "d3"],
        "price": [100.0, 110.0, 132.0],
    })
    # 단일 종목이므로 중앙값 = 자기 자신 -> ret > med 는 항상 False (0)
    labels = ee.market_relative_labels(df, horizon=2)
    assert labels.tolist() == [0, 0, 0]


def test_apply_recent_weight_duplicates_recent_rows():
    X = np.arange(10, dtype=np.float32).reshape(5, 2)
    y = np.array([0, 1, 0, 1, 0])
    dates = np.array(["d1", "d1", "d2", "d2", "d3"])
    Xw, yw = ee.apply_recent_weight(X, y, dates, weight=2.0, recent_days=2)
    # recent = {d2, d3} -> 행 index 2,3,4 (3행) 복제 -> 총 5+3=8
    assert Xw.shape == (8, 2)
    assert len(yw) == 8
    assert int(yw.sum()) == int(y.sum()) + int(y[2:].sum())


def test_apply_recent_weight_noop_when_weight_one():
    X = np.arange(6, dtype=np.float32).reshape(3, 2)
    y = np.array([0, 1, 0])
    Xw, yw = ee.apply_recent_weight(X, y, ["d1", "d2", "d3"], weight=1.0, recent_days=2)
    assert Xw is X
    assert yw is y


def test_apply_recent_weight_noop_when_no_dates():
    X = np.arange(6, dtype=np.float32).reshape(3, 2)
    y = np.array([0, 1, 0])
    Xw, yw = ee.apply_recent_weight(X, y, None, weight=2.0)
    assert Xw is X
    assert yw is y
