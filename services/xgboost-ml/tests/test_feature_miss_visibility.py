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
