"""피처 조회 실패를 삼키지 않는지(가시화) 검증한다."""
from __future__ import annotations

import pathlib
import re

PIPE = pathlib.Path(__file__).resolve().parents[1] / "app/feature_engine/feature_pipeline.py"


def test_no_silent_zero_fallbacks() -> None:
    src = PIPE.read_text(encoding="utf-8")
    silent = re.findall(r'logger\.debug\("[A-Za-z0-9_]+ unavailable; using 0\.0"\)', src)
    assert silent == [], silent


def test_miss_helper_exists_and_reports() -> None:
    src = PIPE.read_text(encoding="utf-8")
    assert "def _note_miss(" in src
    assert "def feature_miss_report(" in src
    assert "atexit.register(_dump_miss_report)" in src


def test_group_failures_are_counted() -> None:
    src = PIPE.read_text(encoding="utf-8")
    for group in ("group:sns_features", "group:advanced_features", "group:market_data"):
        assert group in src, group


def test_sentiment_uses_sns_source() -> None:
    src = PIPE.read_text(encoding="utf-8")
    body = src.split("def _get_stock_sentiment")[1].split("def _get_economic_events")[0]
    assert "sns_post_features" in body
    assert "stock_sentiment" in body
    assert body.index("sns_post_features") < body.index("stock_sentiment")


def test_short_features_use_real_source() -> None:
    src = PIPE.read_text(encoding="utf-8")
    assert "krx_short_selling" in src
    assert "FROM short_interest\n" not in src
    assert "FROM margin_balance\n" not in src
    assert "FROM credit_balance\n" not in src
