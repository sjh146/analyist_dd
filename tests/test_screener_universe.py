"""screener_universe (백분위 유니버스 파일) 단위 테스트.

왜: 이 파일 형식은 스크리너(컨테이너)와 발행측이 공유하는 계약의 일부다. 형식이나
백분위 계산이 어긋나면 트레이더의 R1 문턱이 조용히 다른 의미가 된다.
"""
import json
import os
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import screener_universe as su  # noqa: E402


def test_dump_and_load_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "swing_universe_2026-09-28.json")

        written = su.dump_universe(
            [{"stock_code": "475830", "confidence": 0.6245},
             {"stock_code": "298380", "confidence": 0.5804}],
            path, "2026-09-28")

        assert written == path
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        assert payload["scored"] == 2
        assert su.load_scores(path) == {"475830": 0.6245, "298380": 0.5804}


def test_dump_skips_rows_without_usable_confidence():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "u.json")

        su.dump_universe(
            [{"stock_code": "475830", "confidence": 0.6},
             {"stock_code": "298380"},
             {"stock_code": "", "confidence": 0.7},
             {"stock_code": "000660", "confidence": "0.55"}],
            path, "2026-09-28")

        assert su.load_scores(path) == {"475830": 0.6, "000660": 0.55}


def test_dump_returns_none_without_scores():
    with tempfile.TemporaryDirectory() as tmp:
        assert su.dump_universe([], os.path.join(tmp, "u.json"), "2026-09-28") is None
        assert su.dump_universe(None, os.path.join(tmp, "u.json"), "2026-09-28") is None


def test_load_missing_or_broken_file_is_empty():
    with tempfile.TemporaryDirectory() as tmp:
        assert su.load_scores(os.path.join(tmp, "nope.json")) == {}
        broken = os.path.join(tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        assert su.load_scores(broken) == {}


def test_percentile_ranks_top_and_bottom():
    population = [0.10, 0.20, 0.30, 0.40, 0.50]

    assert su.percentile_of(0.50, population) == pytest.approx(90.0)
    assert su.percentile_of(0.10, population) == pytest.approx(10.0)
    assert su.percentile_of(0.30, population) == pytest.approx(50.0)


def test_percentile_ties_use_mid_rank():
    population = [0.5, 0.5, 0.5, 0.5]

    assert su.percentile_of(0.5, population) == pytest.approx(50.0)


def test_percentile_handles_missing_inputs():
    assert su.percentile_of(None, [0.1, 0.2]) is None
    assert su.percentile_of(0.5, []) is None
    assert su.percentile_of(0.5, [None, None]) is None
