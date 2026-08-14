"""Feature contract growth guard.

Guards the 2026-08 feature-name contract: ``get_feature_names()`` must stay
sorted and must preserve every existing feature name declared in
``feature_names.json``. New features are added to ``get_feature_names()``
(additive) and only become part of ``feature_names.json`` after a retrain
(Phase 6). This test therefore asserts:

1. ``get_feature_names()`` returns a sorted list.
2. Every name in ``feature_names.json`` is still present in
   ``get_feature_names()`` (no existing name removed or renamed).
3. ``get_feature_names()`` contains the new news-event features.
"""

import json
import os

CHAMPION_DIR = "app/models/champion"


def _champion_feature_names() -> list:
    with open(os.path.join(CHAMPION_DIR, "feature_names.json")) as f:
        return json.load(f)


def _get_feature_names() -> list:
    from app.feature_engine.feature_pipeline import FeaturePipeline
    return FeaturePipeline().get_feature_names()


def test_get_feature_names_is_sorted():
    names = _get_feature_names()
    assert names == sorted(names), "get_feature_names() must return a sorted list"


def test_get_feature_names_has_no_duplicates():
    names = _get_feature_names()
    assert len(set(names)) == len(names), "get_feature_names() has duplicate names"


def test_existing_feature_names_preserved():
    """Every name in feature_names.json must still be in get_feature_names()."""
    json_names = _champion_feature_names()
    pipeline_names = set(_get_feature_names())
    missing = [n for n in json_names if n not in pipeline_names]
    assert not missing, f"Existing feature names removed from get_feature_names(): {missing}"


def test_new_news_event_features_present():
    """The Phase 5 news-event features must be exposed by get_feature_names()."""
    names = set(_get_feature_names())
    expected = {
        "market_impact_score",
        "event_realized_5d",
        "event_mna_5d",
        "event_capital_increase_5d",
        "event_cb_bw_5d",
        "event_stake_change_5d",
        "event_contract_5d",
        "event_new_product_5d",
        "event_patent_5d",
        "event_regulation_5d",
        "event_litigation_5d",
        "event_delisting_5d",
        "event_recall_5d",
        "event_treasury_5d",
        "event_exec_change_5d",
        "event_partnership_5d",
        "event_macro_5d",
        "event_market_liquidity_5d",
        "event_disaster_5d",
        "theme_exposure_5d",
    }
    missing = expected - names
    assert not missing, f"New news-event features missing from get_feature_names(): {missing}"
