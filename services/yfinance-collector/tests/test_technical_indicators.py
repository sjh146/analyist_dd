"""기술지표 계산 회귀 테스트 — macd·볼린저·스토캐스틱이 실제로 생성되는가.

왜 이 테스트가 있는가 (2026-09-28 실측 사고):
    pandas 2.x 에서 ``groupby(...).apply(func)`` 가 DataFrame 을 돌려줄 때 결과 구조가 바뀌어
    ``result.xs(col, level=-1)`` 가 ``KeyError: 'macd'`` 로 죽었다. ``calculate_all`` 이 그 예외를
    로그로만 남기고 중단해 **macd/macd_signal/macd_hist·볼린저 4종·stoch_k/stoch_d 가 통째로
    생성되지 않았다**(컨테이너 로그: `Failed to calculate indicators: 'macd'`).
    ML 쪽은 지표 컬럼이 없으면 상수 폴백(macd=0, stoch_k=50)을 쓰므로 그 피처들이 '죽은 피처'가 된다.

이 테스트는 컬럼 존재만 보지 않고 **그룹 경계를 넘지 않는지**까지 본다 — 종목이 바뀌는 지점에서
이전 종목의 값으로 이어지면(shift/rolling 누출) 검증된 지표가 아니다.
"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from app.processors.technical_indicators import TechnicalIndicatorCalculator

EXPECTED = [
    "sma_5", "sma_20", "sma_60", "ema_12", "ema_26",
    "rsi", "macd", "macd_signal", "macd_hist",
    "bb_middle", "bb_upper", "bb_lower", "bb_width",
    "stoch_k", "stoch_d", "atr", "obv",
]


class TechnicalIndicatorsTest(unittest.TestCase):
    def _frame(self, stocks=("A005930", "A000660"), days=80) -> pd.DataFrame:
        rows = []
        rng = np.random.default_rng(7)
        for index, code in enumerate(stocks):
            price = 10_000.0 * (index + 1)
            for day in range(days):
                price = max(100.0, price * float(1 + rng.normal(0.001, 0.02)))
                rows.append(
                    {
                        "stock_code": code,
                        "trade_date": pd.Timestamp("2026-05-01") + pd.Timedelta(days=day),
                        "open": price * 0.995,
                        "high": price * 1.02,
                        "low": price * 0.98,
                        "close": price,
                        "volume": int(100_000 + day * 10),
                    }
                )
        return pd.DataFrame(rows)

    def test_calculate_all_produces_every_indicator(self) -> None:
        """macd·볼린저·스토캐스틱이 사라지면(예외로 중단) 여기서 실패한다."""
        out = TechnicalIndicatorCalculator().calculate_all(self._frame())
        missing = [col for col in EXPECTED if col not in out.columns]
        self.assertEqual(missing, [], f"생성되지 않은 지표: {missing}")

    def test_values_are_finite_after_warmup(self) -> None:
        out = TechnicalIndicatorCalculator().calculate_all(self._frame())
        tail = out.groupby("stock_code").tail(10)
        for col in ("macd", "macd_hist", "bb_width", "stoch_k", "rsi", "atr"):
            values = pd.to_numeric(tail[col], errors="coerce")
            self.assertTrue(
                np.isfinite(values.fillna(0.0)).all(),
                f"{col} 에 NaN/inf 가 남아 있다(롤링 창 부족을 0 으로 채워야 한다)",
            )

    def test_single_stock_frame_still_produces_every_indicator(self) -> None:
        """종목이 하나뿐인 프레임에서도 모든 지표가 생성되어야 한다 (ML 쪽 호출 패턴).

        ``groupby(...).apply(...).reset_index(level=0)`` 방식은 그룹이 하나면 apply 가 DataFrame 을
        돌려주며 ValueError 로 죽어 **atr/obv 가 조용히 사라졌다**(2026-09-28 실측). ML 의 feature
        빌더는 종목별로 호출하므로 그 경로에서 이 두 피처가 늘 죽어 있었다.
        """
        frame = self._frame(stocks=("A005930",), days=60)
        out = TechnicalIndicatorCalculator().calculate_all(frame)
        missing = [col for col in EXPECTED if col not in out.columns]
        self.assertEqual(missing, [], f"단일 종목 프레임에서 누락: {missing}")
        tail = out.tail(5)
        for col in ("atr", "obv", "macd", "stoch_k"):
            values = pd.to_numeric(tail[col], errors="coerce")
            self.assertTrue(values.notna().any(), f"{col} 가 전부 NaN 이다")


    def test_groupwise_equals_single_stock(self) -> None:
        """그룹 경계 누출 검사: 전체 프레임 계산 == 종목별 단독 계산 (지표별로 완전 일치).

        누출(shift/rolling 이 종목을 넘어가는 것)이나 인덱스 어긋남이 있으면 여기서 깨진다.
        """
        frame = self._frame()
        whole = TechnicalIndicatorCalculator().calculate_all(frame)
        for code, group in frame.groupby("stock_code"):
            alone = TechnicalIndicatorCalculator().calculate_all(group.reset_index(drop=True).copy())
            for col in ("macd", "macd_signal", "macd_hist", "bb_upper", "bb_width",
                        "stoch_k", "rsi", "atr"):
                merged = whole[whole["stock_code"] == code].reset_index(drop=True)[col]
                pd.testing.assert_series_equal(
                    merged, alone[col], check_names=False, rtol=1e-9, atol=1e-9,
                    obj=f"{code}.{col}",
                )


if __name__ == "__main__":
    unittest.main()
