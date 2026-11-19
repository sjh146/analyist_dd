"""tests/test_patch_panel_short.py — 공매도 비중 컬럼 패치 (4 tests)."""
import json
import os
import sys

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import patch_panel_short as pps  # noqa: E402


def _panel(tmp_path, values):
    X = np.zeros((3, 2), dtype=np.float32)
    X[:, 1] = values
    p = tmp_path / "p.npz"
    np.savez_compressed(p, X=X, feature_names=np.array(["a", "short_selling_ratio"]),
                        dates=np.array(["2026-11-03", "2026-11-04", "2026-11-05"]),
                        codes=np.array(["000001", "000001", "000001"]),
                        price=np.ones(3))
    return p


def test_asof_uses_previous_day_only():
    series = {"000001": [("2026-11-03", 0.05), ("2026-11-04", 0.09)]}
    codes = ["000001", "000001"]
    dates = ["2026-11-04", "2026-11-05"]
    vals = pps.asof_values(codes, dates, series)
    assert vals[0] == pytest.approx(0.05)   # 당일(11-04) 값이 아니라 직전(11-03)
    assert vals[1] == pytest.approx(0.09)


def test_stale_source_becomes_nan():
    series = {"000001": [("2026-09-01", 0.04)]}
    vals = pps.asof_values(["000001"], ["2026-11-05"], series, max_stale_days=10)
    assert np.isnan(vals[0])                 # 2달 전 값은 끌고 오지 않는다


def test_out_equals_panel_is_rejected(tmp_path):
    p = _panel(tmp_path, [1.0, 2.0, 3.0])
    assert pps.main(["--panel", str(p), "--out", str(p)]) == 2


def test_patch_replaces_only_target_column(tmp_path, monkeypatch):
    p = _panel(tmp_path, [1.0, 2.0, 3.0])
    out = tmp_path / "o.npz"
    monkeypatch.setattr(pps, "load_short", lambda db, codes, max_date: {
        "000001": [("2026-11-02", 0.10), ("2026-11-03", 0.20), ("2026-11-04", 0.30)]})
    monkeypatch.setattr(pps, "_pg", lambda: type("C", (), {"close": lambda self: None})())
    assert pps.main(["--panel", str(p), "--out", str(out),
                     "--report", str(tmp_path / "r.json")]) == 0
    z = np.load(out, allow_pickle=True)
    assert z["X"][0, 1] == pytest.approx(0.0) or z["X"][0, 1] == pytest.approx(0.10)
    assert np.all(z["X"][:, 0] == 0)          # 비대상 컬럼 불변
    rep = json.loads((tmp_path / "r.json").read_text())
    assert rep["after"]["nonzero_rows"] >= 1
