"""개장 게이트·단타 목록 비우기 회귀."""
from __future__ import annotations

import importlib.util
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_open_gate_threshold_decision():
    gate = _load("kosdaq_gate", "scripts/kosdaq_open_gate.py")
    assert gate.decide(0.5, 0.5) is True
    assert gate.decide(1.2, 0.5) is True
    assert gate.decide(0.49, 0.5) is False
    assert gate.decide(-1.58, 0.5) is False
    assert gate.decide(None, 0.5) is False


def test_clear_daytrading_keeps_other_screeners(tmp_path):
    clear_mod = _load("feed_clear", "scripts/feed_clear_daytrading.py")
    path = tmp_path / "feed.json"
    path.write_text(json.dumps({
        "generated_at": "2026-11-24T09:10:00+09:00",
        "candidates": {
            "close": {"items": [{"stock_code": "005930"}]},
            "swing": {"items": [{"stock_code": "000660"}]},
            "daytrading": {"items": [{"stock_code": "035720"}, {"stock_code": "068270"}]},
        },
    }), encoding="utf-8")
    out = clear_mod.clear(str(path))
    assert out["cleared_from"] == 2
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["candidates"]["daytrading"]["items"] == []
    assert saved["candidates"]["daytrading"]["reason"] == "blocked:open_gate"
    assert len(saved["candidates"]["close"]["items"]) == 1
    assert len(saved["candidates"]["swing"]["items"]) == 1


def test_pipeline_wires_open_gate():
    text = (REPO / "cron/daytrading_pipeline.sh").read_text(encoding="utf-8")
    assert "DAYTRADING_OPEN_GATE_PCT" in text
    assert "kosdaq_open_gate.py" in text
    assert "feed_clear_daytrading.py" in text
