"""ATR 하한 진입 필터 (2026-11-28).

실측 근거: 0.75%/5% 기준 ATR 분위 순기대가 Q1(0.31~4.75%) +0.068% → Q5(9.07%+) +0.611%
로 단조 증가(8,499 종목·일). 하위 구간은 목표 도달이 4.2%뿐이라 기대값이 사실상 0이다.
"""
from __future__ import annotations

import pandas as pd

from scripts.day_trading_engine.scoring import compute_atr_pct


def _bars(code: str, closes: list[float], spread: float = 0.02) -> pd.DataFrame:
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "stock_code": code,
            "trade_date": pd.Timestamp("2026-11-01") + pd.Timedelta(days=i),
            "open_price": c, "high_price": c * (1 + spread / 2),
            "low_price": c * (1 - spread / 2), "close_price": c,
            "volume": 10_000, "trading_value": c * 10_000,
        })
    return pd.DataFrame(rows)


def test_atr_pct_normalizes_to_close():
    df = pd.concat([_bars("000001", [10_000.0] * 20), _bars("000002", [10_000.0] * 20, spread=0.10)])
    out = compute_atr_pct(df).set_index("stock_code")["atr_pct_pct"]
    assert abs(out["000001"] - 2.0) < 0.2
    assert abs(out["000002"] - 10.0) < 0.6
    assert out["000002"] > out["000001"]


def test_atr_pct_empty_frame_returns_contract_columns():
    out = compute_atr_pct(pd.DataFrame())
    assert list(out.columns) == ["stock_code", "atr_pct_pct"]
    assert out.empty


def test_atr_pct_uses_last_row_only():
    df = _bars("000003", [10_000.0] * 20)
    out = compute_atr_pct(df)
    assert len(out) == 1
