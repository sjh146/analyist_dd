"""feature_coverage_report 가 rank_* 6개를 측정 대상에 포함하는지.

실측 2026-11-24 (R49): `feature_coverage` 202행 중 6행(rank_return_5d·return_20d·
volatility_20d·volume_ratio_5·volume_ratio_20·ma_position_5)이 11-18 측정에 고정돼
`dq_feature_oldest_age_days` 가 5일 문턱에 붙었다. 원인은 rank_* 가
`build_training_features` 패널에 없고 `compute_cross_sectional_ranks`(스크리너·트레이너 경로)
에서만 생성되기 때문 — 이 리포트로는 영원히 갱신 불가였다.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import feature_coverage_report as fcr  # noqa: E402


def _panel():
    return pd.DataFrame({
        "stock_code": ["A", "B", "C", "A", "B", "C"],
        "date": ["2026-11-20"] * 3 + ["2026-11-21"] * 3,
        "return_5d": [0.1, 0.2, 0.3, 0.1, 0.3, 0.2],
        "volume_ratio_20": [1.0, 2.0, 3.0, 1.5, 2.5, 3.5],
        "unrelated_col": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })


def test_ranks_added_per_date():
    """순위 소스가 없는 컬럼은 만들지 않고, 날짜별 백분위 (1/3, 2/3, 1.0) 이 나온다."""
    df = fcr._add_cross_sectional_ranks(_panel())
    assert "rank_return_5d" in df.columns
    assert "rank_volume_ratio_20" in df.columns
    assert "rank_unrelated_col" not in df.columns
    for _, g in df.groupby("date"):
        assert sorted(round(v, 6) for v in g["rank_return_5d"]) == [round(1 / 3, 6), round(2 / 3, 6), 1.0]


def test_ranks_are_all_nonzero_and_not_constant():
    """상수 0 으로 측정되면 '죽은 피처'로 오판된다 — 실제로는 살아있는 피처다."""
    df = fcr._add_cross_sectional_ranks(_panel())
    col = df["rank_return_5d"]
    assert (col != 0).all()
    assert col.std(ddof=0) > 0


def test_no_date_column_is_noop():
    df = pd.DataFrame({"return_5d": [0.1, 0.2]})
    out = fcr._add_cross_sectional_ranks(df)
    assert "rank_return_5d" not in out.columns
