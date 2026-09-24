"""Tests for guarded champion promotion (candidate → champion).

The rule under test: a challenger may only replace the champion when its val AUC
clears an absolute floor AND matches/beats the incumbent. A degenerate model
(val AUC < 0.55) must never be promoted — that failure mode shipped a
near-constant all-DOWN champion in Aug 2026.
"""

import json
import os

import pytest

from app.training.champion_promote import promote


def _write_candidate(
    d: str,
    auc: float,
    models: bool = True,
    result_json: bool = True,
    auc_file: "float | None" = None,
) -> str:
    os.makedirs(d, exist_ok=True)
    if models:
        for name in ("xgboost_model.pkl", "lightgbm_model.pkl", "catboost_model.pkl"):
            with open(os.path.join(d, name), "wb") as f:
                f.write(b"stub")
    with open(os.path.join(d, "feature_names.json"), "w") as f:
        json.dump(["return_5d", "volume_ratio_5"], f)
    with open(os.path.join(d, "auc.txt"), "w") as f:
        f.write(f"{(auc if auc_file is None else auc_file):.6f}\n")
    if result_json:
        with open(os.path.join(d, "training-result-20260923-210000.json"), "w") as f:
            json.dump({
                "retrained_at": "2026-09-23T21:00:00",
                "n_rows": 16108,
                "n_features": 173,
                "up_rate": 0.46,
                "model_aucs": {"xgboost": 0.58, "lightgbm": 0.61, "catboost": 0.53},
                "ensemble_auc": auc,
            }, f)
    return d


def _write_champion(d: str, auc: float) -> str:
    os.makedirs(d, exist_ok=True)
    for name in ("xgboost_model.pkl", "lightgbm_model.pkl", "catboost_model.pkl"):
        with open(os.path.join(d, name), "wb") as f:
            f.write(b"incumbent")
    with open(os.path.join(d, "feature_names.json"), "w") as f:
        json.dump(["return_5d"], f)
    with open(os.path.join(d, "auc.txt"), "w") as f:
        f.write(f"{auc:.6f}\n")
    return d


def test_promotes_better_candidate(tmp_path):
    cand = _write_candidate(str(tmp_path / "cand"), auc=0.62)
    champ = _write_champion(str(tmp_path / "champion"), auc=0.61)

    res = promote(cand, champ, min_auc=0.55)

    assert res["promoted"] is True
    assert res["status"] == "promoted"
    with open(os.path.join(champ, "auc.txt")) as f:
        assert abs(float(f.read().strip()) - 0.62) < 1e-6
    # incumbent preserved for manual revert
    assert res["backup_dir"] and os.path.isdir(res["backup_dir"])
    with open(os.path.join(res["backup_dir"], "auc.txt")) as f:
        assert abs(float(f.read().strip()) - 0.61) < 1e-6


def test_degenerate_candidate_is_rejected(tmp_path):
    """val AUC 0.43 (the Aug 2026 collapse) must not become champion."""
    cand = _write_candidate(str(tmp_path / "cand"), auc=0.4365)
    champ = _write_champion(str(tmp_path / "champion"), auc=0.6131)

    res = promote(cand, champ, min_auc=0.55)

    assert res["promoted"] is False
    assert res["status"] == "kept_incumbent"
    with open(os.path.join(champ, "auc.txt")) as f:
        assert abs(float(f.read().strip()) - 0.6131) < 1e-6


def test_worse_but_above_floor_candidate_is_rejected(tmp_path):
    cand = _write_candidate(str(tmp_path / "cand"), auc=0.58)
    champ = _write_champion(str(tmp_path / "champion"), auc=0.6131)

    res = promote(cand, champ, min_auc=0.55)

    assert res["promoted"] is False
    assert "did not beat incumbent" in res["reason"]


def test_higher_floor_allows_promotion_when_better(tmp_path):
    cand = _write_candidate(str(tmp_path / "cand"), auc=0.645)
    champ = _write_champion(str(tmp_path / "champion"), auc=0.6131)

    res = promote(cand, champ, min_auc=0.60, min_improvement=0.02)

    assert res["promoted"] is True


def test_missing_artifacts_makes_candidate_invalid(tmp_path):
    cand = _write_candidate(str(tmp_path / "cand"), auc=0.70, models=False)
    champ = _write_champion(str(tmp_path / "champion"), auc=0.50)

    res = promote(cand, champ, min_auc=0.55)

    assert res["promoted"] is False
    assert res["status"] == "invalid_candidate"
    assert "xgboost_model.pkl" in res["reason"]


def test_auc_file_mismatch_is_rejected(tmp_path):
    """auc.txt and training-result disagreement means a half-written candidate."""
    cand = _write_candidate(str(tmp_path / "cand"), auc=0.70, auc_file=0.20)
    champ = _write_champion(str(tmp_path / "champion"), auc=0.50)

    res = promote(cand, champ, min_auc=0.55)

    assert res["promoted"] is False
    assert res["status"] == "invalid_candidate"


def test_dry_run_leaves_champion_untouched(tmp_path):
    cand = _write_candidate(str(tmp_path / "cand"), auc=0.70)
    champ = _write_champion(str(tmp_path / "champion"), auc=0.50)

    res = promote(cand, champ, min_auc=0.55, dry_run=True)

    assert res["promoted"] is False
    assert res["status"] == "would_promote"
    with open(os.path.join(champ, "auc.txt")) as f:
        assert abs(float(f.read().strip()) - 0.50) < 1e-6
