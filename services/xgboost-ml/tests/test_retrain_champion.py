"""Tests for the deterministic champion retrain (train/inference contract).

Verifies that retrain_champion() trains all models on the SAME canonical
feature matrix, persists feature_names.json in that exact order, and that the
resulting champion can predict on a json-order vector (the screener's path).
"""

import json
import os

import numpy as np
import pandas as pd
import pytest

from app.training.retrain_champion import retrain_champion


def _synthetic_panel(n_stocks: int = 6, n_dates: int = 40) -> pd.DataFrame:
    rng = np.random.RandomState(11)
    dates = pd.date_range("2026-01-01", periods=n_dates, freq="B").strftime("%Y-%m-%d")
    rows = []
    for s in range(n_stocks):
        price = 100.0 + np.cumsum(rng.normal(0.01, 1.2, n_dates))
        price = np.maximum(price, 20.0)
        for i, d in enumerate(dates):
            rows.append({
                "date": d,
                "stock_code": f"{s:06d}",
                "price": price[i],
                "return_5d": price[i] / price[max(0, i - 5)] - 1 if i >= 5 else 0.0,
                "return_20d": price[i] / price[max(0, i - 20)] - 1 if i >= 20 else 0.0,
                "volatility_20d": 0.02 + rng.rand() * 0.02,
                "volume_ratio_5": 0.9 + rng.rand() * 0.3,
                "volume_ratio_20": 0.95 + rng.rand() * 0.2,
                "ma_position_5": rng.randn() * 0.5,
                "ma_position_20": rng.randn() * 0.5,
            })
    return pd.DataFrame(rows)


def test_retrain_persists_contract(tmp_path):
    from app.feature_engine.feature_pipeline import FeaturePipeline

    df = _synthetic_panel()
    meta = retrain_champion(df, out_dir=str(tmp_path), val_frac=0.2)

    # Canonical contract == the fixed sorted feature list (same order as the screener).
    canonical = FeaturePipeline().get_feature_names()
    with open(os.path.join(tmp_path, "feature_names.json")) as f:
        saved = json.load(f)
    assert saved == canonical, "feature_names.json must be the canonical order"

    for name in ("xgboost_model.pkl", "lightgbm_model.pkl", "catboost_model.pkl", "auc.txt"):
        assert os.path.exists(os.path.join(tmp_path, name)), f"missing {name}"

    assert meta["n_features"] == len(canonical)
    assert 0.0 <= meta["ensemble_auc"] <= 1.0
    assert meta["up_rate"] > 0.0


def test_retrained_champion_predicts_on_json_order(tmp_path):
    from app.models.ensemble_model import EnsembleModel

    df = _synthetic_panel()
    retrain_champion(df, out_dir=str(tmp_path), val_frac=0.2)

    ensemble = EnsembleModel(model_dir=str(tmp_path))
    ensemble.load(str(tmp_path))
    assert ensemble._is_trained

    # Screener builds a vector in json order — every model must accept it.
    with open(os.path.join(tmp_path, "feature_names.json")) as f:
        fn = json.load(f)
    X = np.zeros((2, len(fn)), dtype=np.float32)
    probs = ensemble.predict(X)
    assert probs.shape == (2,)
    assert np.all(np.isfinite(probs))
