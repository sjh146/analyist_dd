"""기술지표 계산기(ML 사본) 회귀 테스트 — 볼린저·스토캐스틱이 '죽은 피처'가 아니게 되었는지.

배경(2026-09-28): ``feature_pipeline`` 이 기대하는 기술 피처 8개 중 bb_width·bb_position·
stoch_k·stoch_d 를 이 계산기가 아예 만들지 않아, ``market_features`` 가 기본값으로 떨어졌고
피처 커버리지에서 nonzero_ratio=0 인 죽은 피처로 남아 있었다.
"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from app.processors.technical_indicators import TechnicalIndicatorCalculator

NEW_COLUMNS = ["bb_middle", "bb_upper", "bb_lower", "bb_width", "bb_position",
               "stoch_k", "stoch_d"]


def _frame(days: int = 90, code: str = "A005930") -> pd.DataFrame:
    rng = np.random.default_rng(11)
    price = 10_000.0
    rows = []
    for day in range(days):
        price = max(100.0, price * float(1 + rng.normal(0.0005, 0.02)))
        rows.append(
            {
                "stock_code": code,
                "trade_date": pd.Timestamp("2026-05-01") + pd.Timedelta(days=day),
                "close_price": price,
                "high_price": price * 1.02,
                "low_price": price * 0.98,
                "open_price": price * 0.995,
                "volume": 100_000 + day,
            }
        )
    return pd.DataFrame(rows)


class MlTechnicalIndicatorsTest(unittest.TestCase):
    def test_new_columns_present_with_db_column_names(self) -> None:
        out = TechnicalIndicatorCalculator().calculate_all(
            _frame(), close_col="close_price", high_col="high_price", low_col="low_price"
        )
        missing = [col for col in NEW_COLUMNS if col not in out.columns]
        self.assertEqual(missing, [], f"생성되지 않은 컬럼: {missing}")

    def test_values_are_not_degenerate(self) -> None:
        """워밍업 이후 값이 전부 NaN 도, 상수(0 고정)도 아니어야 한다 — 죽은 피처 방지."""
        out = TechnicalIndicatorCalculator().calculate_all(
            _frame(), close_col="close_price", high_col="high_price", low_col="low_price"
        ).tail(30)
        for col in NEW_COLUMNS:
            values = pd.to_numeric(out[col], errors="coerce")
            self.assertTrue(values.notna().any(), f"{col} 가 전부 NaN")
            self.assertGreater(float(values.std()), 0.0, f"{col} 가 상수다(분산 0)")

    def test_ranges_are_sane(self) -> None:
        out = TechnicalIndicatorCalculator().calculate_all(
            _frame(), close_col="close_price", high_col="high_price", low_col="low_price"
        ).tail(30)
        stoch = pd.to_numeric(out["stoch_k"], errors="coerce").dropna()
        self.assertTrue(((stoch >= 0) & (stoch <= 100)).all(), "스토캐스틱이 0~100 밖이다")
        width = pd.to_numeric(out["bb_width"], errors="coerce").dropna()
        self.assertTrue((width > 0).all(), "볼린저 폭이 0 이하이다")
        # 밴드 순서: lower <= middle <= upper
        self.assertTrue((out["bb_lower"] <= out["bb_middle"]).all())
        self.assertTrue((out["bb_middle"] <= out["bb_upper"]).all())

    def test_warmup_stays_nan_not_zero(self) -> None:
        """워밍업 구간을 0 으로 채우면 그 자체가 상수 피처가 된다 — NaN 으로 남겨야 한다."""
        out = TechnicalIndicatorCalculator().calculate_all(
            _frame(days=25), close_col="close_price", high_col="high_price", low_col="low_price"
        )
        # 워밍업: 볼린저는 20행, 스토캐스틱은 14행(min_periods=k_period)이 필요하다.
        self.assertTrue(out.head(19)["bb_middle"].isna().all(),
                        "20일 미만인데 볼린저 중간선이 채워졌다")
        self.assertTrue(out.head(13)["stoch_k"].isna().all(),
                        "14일 미만인데 스토캐스틱이 채워졌다")
        self.assertTrue(out.iloc[19]["bb_middle"] == out.iloc[19]["bb_middle"],
                        "20일째인데 볼린저가 아직 NaN 이다")


if __name__ == "__main__":
    unittest.main()
