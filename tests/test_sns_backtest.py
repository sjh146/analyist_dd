"""DB-free unit tests for the SNS lag walk-forward backtest (Phase D script).

Covers:
 1. walk_forward_splits: >= 2 folds, 각 폴드의 train 날짜 < test 날짜 (시간 누출
    없음), 폴드 커버 합집합이 전체 데이터.
 2. build_champion_matrix: champion 순서 0-fill 행렬 계약 (폭 일치, 미존재
    피처 0, 존재 피처 nan->0).
"""
import importlib.util
import os
import sys
from datetime import date, timedelta

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "xgboost-ml"))
sys.path.insert(0, REPO_ROOT)

# ROS 시스템 패키지에 "scripts" 라는 충돌 모듈이 있어 `from scripts.sns_lag_backtest`
# import 가 ROS 의 scripts 패키지를 잡는다. 파일 경로로 직접 로드해 우회한다.
_spec = importlib.util.spec_from_file_location(
    "sns_lag_backtest", os.path.join(REPO_ROOT, "scripts", "sns_lag_backtest.py")
)
_sbt = importlib.util.module_from_spec(_spec)
sys.modules["sns_lag_backtest"] = _sbt
_spec.loader.exec_module(_sbt)
walk_forward_splits = _sbt.walk_forward_splits
build_champion_matrix = _sbt.build_champion_matrix


def _dates(n=40):
    return [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]


def _row_dates_with_dups():
    """같은 날짜에 여러 종목 로우가 있는 실제 패널 형태."""
    base = _dates(20)
    out = []
    for d in base:
        out += [d, d, d]  # 3 종목/일
    return out


def test_walk_forward_min_two_folds():
    dates = _dates(50)
    splits = walk_forward_splits(dates, 2)
    assert len(splits) >= 2


def test_walk_forward_no_leakage_single():
    dates = _dates(30)
    splits = walk_forward_splits(dates, 3)
    for train, test in splits:
        train_dates = [dates[i] for i in train]
        test_dates = [dates[i] for i in test]
        assert max(train_dates) < min(test_dates), "train/test date overlap"


def test_walk_forward_no_leakage_multi_stock():
    # 같은 날짜에 여러 종목 로우 — 날짜 레벨 누출이 없어야 한다.
    dates = _row_dates_with_dups()
    splits = walk_forward_splits(dates, 3)
    assert len(splits) >= 2
    for train, test in splits:
        train_dates = [dates[i] for i in train]
        test_dates = [dates[i] for i in test]
        assert max(train_dates) < min(test_dates)


def test_walk_forward_disjoint_and_covers():
    dates = _dates(40)
    n = len(dates)
    splits = walk_forward_splits(dates, 3)
    covered = set()
    for train, test in splits:
        assert set(train).isdisjoint(test)
        covered.update(train)
        covered.update(test)
    assert covered == set(range(n))


def test_walk_forward_insufficient_data_empty():
    assert walk_forward_splits(_dates(2), 3) == []


def test_champion_matrix_width_and_zeros():
    features = [
        {"x": 1.0, "y": 2.0, "z": 3.0},
        {"x": 4.0, "y": 5.0, "z": 6.0},
    ]
    # champion 순서: ["x", "missing", "z"] → missing 은 0-fill.
    names = ["x", "missing", "z"]
    X = build_champion_matrix(features, names)
    assert X.shape == (2, 3)
    np.testing.assert_allclose(X[:, 0], [1.0, 4.0])
    np.testing.assert_allclose(X[:, 1], [0.0, 0.0])  # 미존재 → 0
    np.testing.assert_allclose(X[:, 2], [3.0, 6.0])


def test_champion_matrix_nan_to_zero():
    features = [
        {"a": float("nan"), "b": float("inf"), "c": 1.0},
        {"a": 2.0, "b": 3.0, "c": -4.0},
    ]
    X = build_champion_matrix(features, ["a", "b", "c"])
    assert X.shape == (2, 3)
    assert np.all(np.isfinite(X))
    np.testing.assert_allclose(X[0, 0], 0.0)
    np.testing.assert_allclose(X[0, 1], 0.0)


def test_champion_matrix_empty_inputs():
    assert build_champion_matrix([], ["a", "b"]).shape == (0, 2)
    assert build_champion_matrix([{"x": 1}], []).shape == (1, 0)
