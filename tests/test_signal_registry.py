import math

import numpy as np
import pytest

from scripts.signal_registry import classify, compute_registry


def _synthetic_panel(n_dates=240, n_codes=60, seed=11):
    rng = np.random.default_rng(seed)
    daily = rng.normal(0.0004, 0.02, size=(n_dates, n_codes))
    price = 10000.0 * np.exp(np.cumsum(daily, axis=0))
    dates = np.array(["D%04d" % i for i in range(n_dates)])
    codes = np.array(["C%04d" % i for i in range(n_codes)])
    return np.repeat(dates, n_codes), np.tile(codes, n_dates), price.ravel(), price, rng


def _rel_fwd5(price, h=5):
    n_dates, n_codes = price.shape
    fwd = np.full((n_dates, n_codes), np.nan)
    fwd[:n_dates - h] = price[h:] / price[:n_dates - h] - 1.0
    return (fwd - np.nanmedian(fwd, axis=1)[:, None]).ravel()


def test_strong_signal_is_alive():
    dates, codes, price_all, price, rng = _synthetic_panel()
    n_dates = price.shape[0]
    rel = _rel_fwd5(price)
    x = rel + 0.5 * np.nanstd(rel) * rng.normal(size=rel.size)
    payload = compute_registry(np.column_stack([x]), ["planted"], dates, codes, price_all)
    f = payload["features"][0]
    assert f["status"] == "alive"
    assert f["ic_h5"] > 0.5
    assert f["t_h5"] > 2.0
    assert f["n_days"] == n_dates - 5
    assert f["coverage"] == pytest.approx((n_dates - 5) / n_dates)
    assert f["top10_net_h5"] > 0.0
    assert payload["summary"] == {"alive": 1, "weak": 0, "dead": 0, "insufficient": 0}


def test_pure_noise_is_dead():
    dates, codes, price_all, price, rng = _synthetic_panel(seed=7)
    x = rng.normal(size=price.size)
    payload = compute_registry(np.column_stack([x]), ["noise"], dates, codes, price_all)
    f = payload["features"][0]
    assert f["status"] == "dead"
    assert abs(f["ic_h5"]) < 0.005
    assert abs(f["t_h5"]) < 1.5
    assert payload["summary"] == {"alive": 0, "weak": 0, "dead": 1, "insufficient": 0}


def test_short_history_is_insufficient():
    assert classify(0.5, 9.9, 1) == "insufficient"
    assert classify(0.5, 9.9, 59) == "insufficient"
    assert classify(0.5, 9.9, 60) == "alive"


def test_classify_boundaries():
    assert classify(0.010, 2.0) == "alive"
    assert classify(-0.010, -2.0) == "alive"
    assert classify(0.011, 2.1) == "alive"
    assert classify(0.009, 5.0) == "weak"
    assert classify(0.010, 1.9) == "weak"
    assert classify(0.005, 0.0) == "weak"
    assert classify(0.004, 1.5) == "weak"
    assert classify(0.001, 2.5) == "weak"
    assert classify(0.0049, 1.49) == "dead"
    assert classify(0.0, 0.0) == "dead"
    assert classify(float("nan"), float("nan")) == "dead"


def test_nan_mask_keeps_signal_alive_lowers_coverage():
    dates, codes, price_all, price, rng = _synthetic_panel()
    rel = _rel_fwd5(price)
    x = rel + 0.5 * np.nanstd(rel) * rng.normal(size=rel.size)
    x[rng.random(x.size) < 0.30] = np.nan
    payload = compute_registry(np.column_stack([x]), ["masked"], dates, codes, price_all)
    f = payload["features"][0]
    assert f["status"] == "alive"
    assert f["coverage"] == pytest.approx(0.70, abs=0.02)


def test_payload_schema():
    dates, codes, price_all, price, rng = _synthetic_panel(n_dates=60, n_codes=40)
    rel = _rel_fwd5(price)
    x = rel + 0.5 * np.nanstd(rel) * rng.normal(size=rel.size)
    payload = compute_registry(np.column_stack([x]), ["f1"], dates, codes, price_all)
    assert set(payload) == {"rows", "features", "summary"}
    f = payload["features"][0]
    assert set(f) == {"name", "ic_h1", "ic_h5", "ic_h20", "t_h5", "coverage", "top10_net_h5",
                      "top10_excess_h5", "status", "n_days"}
    assert payload["rows"] == 60 * 40
    assert sum(payload["summary"].values()) == 1
    assert f["t_h5"] is None or math.isfinite(f["t_h5"])
    for v in (f["ic_h1"], f["ic_h5"], f["ic_h20"], f["coverage"], f["top10_net_h5"]):
        assert v is None or (math.isfinite(v) and abs(v) <= 1.0)
