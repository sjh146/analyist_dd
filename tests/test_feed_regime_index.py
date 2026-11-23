"""피드 regime 의 지수 등락률·market_up 발행 테스트 (2026-11-23 사용자 지적).

WHY: 트레이더의 "수익 중 시한부 청산 연장"은 **장이 오른 날에만** 허용한다.
트레이더(Windows)는 지수를 직접 못 받으므로 피드가 market_up 을 실어 보내야 한다.
"""
from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))


def _load_feed_export():
    spec = importlib.util.spec_from_file_location(
        "feed_export_under_test", REPO / "scripts" / "feed_export.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)     # 모듈 로드만 (발행 없음)
    return mod


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _payload(kospi: float | None, kosdaq: float | None) -> dict:
    datas = []
    if kospi is not None:
        datas.append({"itemCode": "KOSPI", "fluctuationsRatio": str(kospi)})
    if kosdaq is not None:
        datas.append({"itemCode": "KOSDAQ", "fluctuationsRatio": str(kosdaq)})
    return {"datas": datas}


@pytest.fixture(scope="module")
def mod():
    return _load_feed_export()


def test_market_up_true_when_kosdaq_positive(mod, monkeypatch) -> None:
    monkeypatch.setenv("FEED_INDEX_QUOTE", "1")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp(_payload(-1.2, 0.8)))
    out = mod._index_day_change()
    assert out["kosdaq_change_pct"] == 0.8
    assert out["kospi_change_pct"] == -1.2
    assert out["market_up"] is True


def test_market_up_false_when_kosdaq_negative(mod, monkeypatch) -> None:
    monkeypatch.setenv("FEED_INDEX_QUOTE", "1")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp(_payload(1.0, -2.6)))
    out = mod._index_day_change()
    assert out["market_up"] is False


def test_kosdaq_missing_falls_back_to_kospi(mod, monkeypatch) -> None:
    monkeypatch.setenv("FEED_INDEX_QUOTE", "1")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp(_payload(0.4, None)))
    out = mod._index_day_change()
    assert out["market_up"] is True
    assert "kosdaq_change_pct" not in out


def test_failure_is_silent_and_disables_extension(mod, monkeypatch) -> None:
    monkeypatch.setenv("FEED_INDEX_QUOTE", "1")

    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert mod._index_day_change() == {}      # market_up 부재 → 트레이더는 연장 안 함


def test_env_off_skips_call(mod, monkeypatch) -> None:
    monkeypatch.setenv("FEED_INDEX_QUOTE", "off")
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        raise AssertionError("호출되면 안 된다")

    monkeypatch.setattr("urllib.request.urlopen", spy)
    assert mod._index_day_change() == {}
    assert called["n"] == 0
