"""momentum_3_12m 이 좁은 창(since 하한)으로 조용히 죽던 결함의 회귀 테스트.

실측 2026-11-19: cron/supply_features.sh 가 `--since 45일 전` 으로 빌더를 돌리자
window_days=43 창 안에서 momentum_3_12m nonzero 가 0.0000(전부 NULL)이 됐다.
원천(market_data)은 정상인데 **로드 창이 252거래일보다 짧아서** shift(252) 가 전부
NaN 이 된 것이므로, 로드 시작일은 격자 하한과 분리돼야 한다.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import build_supply_market_features as bsf  # noqa: E402


def _frame(n_days, start="2026-01-01"):
    dates = pd.bdate_range(start, periods=n_days).date
    close = np.linspace(10000, 20000, n_days)
    return pd.DataFrame({
        "stock_code": "000001",
        "trade_date": dates,
        "open_price": close, "high_price": close, "low_price": close,
        "close_price": close, "volume": 1000.0,
    })


def test_momentum_alive_with_enough_history():
    """252거래일 이상 이력이 있으면 12개월 모멘텀 원재료가 값으로 채워진다.

    momentum_3_12m = _ret_12m - _ret_3m 이므로 두 원재료가 살아야 피처가 산다.
    """
    out = bsf.price_features(_frame(300))
    assert out["_ret_12m"].notna().sum() > 0
    assert out["_ret_3m"].notna().sum() > 0
    mom = (out["_ret_12m"] - out["_ret_3m"]).dropna()
    assert len(mom) > 0 and (mom != 0).any()


def test_momentum_dead_without_lookback():
    """회귀 재현: 창이 252거래일보다 짧으면(45일 크론) 모멘텀이 전부 NaN 이 된다."""
    out = bsf.price_features(_frame(43))
    assert out["_ret_12m"].isna().all()
    assert out["_ret_3m"].isna().all()


def test_lookback_covers_twelve_month_shift():
    """상수가 다시 좁아지지 않게: 선행 이력은 252거래일을 달력일로 환산해 덮어야 한다."""
    # 거래일 → 달력일은 대략 1.4~1.5배(주말+공휴일). 최소 1.4배를 요구한다.
    assert bsf.PRICE_LOOKBACK_DAYS >= bsf.MOM_12M * 1.4


def test_price_history_start_is_before_since():
    """로드 시작일은 격자 하한보다 반드시 앞선다(str/date 둘 다)."""
    for since in ("2026-10-05", pd.Timestamp("2026-10-05").date()):
        start = bsf.price_history_start(since)
        assert start < "2026-10-05"
        assert start == "2025-08-31"


def test_price_history_start_is_deterministic():
    assert bsf.price_history_start("2026-10-05", 400) == bsf.price_history_start(
        "2026-10-05", 400)
