"""Champion consistency canary.

Guards against the production breakage hit Aug 2026: the champion model files
and ``feature_names.json`` drifting out of sync (e.g. models retrained on a
different feature set than the json declares) which makes EVERY prediction
crash with "Feature N is present in model but not in pool".

This test loads the REAL champion from ``app/models/champion`` (the container
mounts the repo, so it tests the actual deployed artifact) and asserts that
every ensemble model can predict on a feature vector of exactly
``feature_names.json`` width — the width the screener/backtester build.
If a retrain or deploy ever breaks the contract again, this test fails red.
"""

import json
import os

import numpy as np

CHAMPION_DIR = "app/models/champion"


def _champion_feature_names() -> list:
    with open(os.path.join(CHAMPION_DIR, "feature_names.json")) as f:
        return json.load(f)


def test_champion_models_predict_on_json_feature_width():
    from app.models.ensemble_model import EnsembleModel

    feature_names = _champion_feature_names()
    assert len(feature_names) >= 10, "feature_names.json looks empty/broken"

    ensemble = EnsembleModel(model_dir=CHAMPION_DIR)
    ensemble.load(CHAMPION_DIR)
    assert ensemble._is_trained, "champion failed to load"

    # Exactly the width the screener builds (champion feature_names.json order).
    X = np.zeros((2, len(feature_names)), dtype=np.float32)

    for name, model in zip(ensemble.model_names, ensemble.models):
        probs = model.predict(X)
        assert len(probs) == 2, f"{name} returned {len(probs)} predictions"
        assert np.all(np.isfinite(probs)), f"{name} produced non-finite probs"
        assert np.all((probs >= 0.0) & (probs <= 1.0)), f"{name} probs out of [0,1]"

    # The ensemble end-to-end path (what the screener calls) must work too.
    ensemble_probs = ensemble.predict(X)
    assert ensemble_probs.shape == (2,)
    assert np.all(np.isfinite(ensemble_probs))


def test_champion_feature_names_json_is_valid():
    feature_names = _champion_feature_names()
    assert isinstance(feature_names, list)
    assert len(set(feature_names)) == len(feature_names), "duplicate feature names"
    for name in feature_names:
        assert isinstance(name, str) and name, f"bad feature name: {name!r}"
