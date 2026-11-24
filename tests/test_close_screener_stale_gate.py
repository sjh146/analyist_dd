"""데이터 나이 게이트 검증 — 낡은 종목이 후보에서 빠지는가."""
from __future__ import annotations

import datetime

import pandas as pd

from scripts.close_screener import drop_stale_codes


def _frames():
    trade_date = datetime.date(2026, 11, 23)
    price = pd.DataFrame({
        "stock_code": ["A", "A", "B", "B", "C"],
        "trade_date": [
            datetime.date(2026, 11, 21), datetime.date(2026, 11, 23),
            datetime.date(2026, 2, 2), datetime.date(2026, 2, 3),
            datetime.date(2026, 9, 1),
        ],
        "close_price": [100.0] * 5,
    })
    ind = pd.DataFrame({"stock_code": ["A", "B", "C"], "score": [1.0, 2.0, 3.0]})
    return ind, price, trade_date


def test_stale_codes_are_dropped():
    ind, price, trade_date = _frames()
    kept, stale = drop_stale_codes(ind, price, trade_date)
    assert stale == {"B", "C"}
    assert list(kept["stock_code"]) == ["A"]


def test_fresh_codes_are_kept():
    ind, price, trade_date = _frames()
    kept, stale = drop_stale_codes(ind, price, trade_date, max_gap_days=400)
    assert stale == set()
    assert len(kept) == 3


def test_empty_price_frame_is_noop():
    ind, _, trade_date = _frames()
    kept, stale = drop_stale_codes(ind, pd.DataFrame(), trade_date)
    assert stale == set()
    assert len(kept) == len(ind)
