"""train_curated.py 의 순수 로직 검증 (합성 패널, DB/모델 학습 불필요).

검증 대상:
  (a) curated 피처 교집합 선택 (sentiment/news 포함/제외)
  (b) 멀티시드 평균±std 집계
  (c) champion_promote 호환 산출물 스키마
"""

import json
import os
import sys

import numpy as np
import pytest

_CANDIDATES = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")),
]
for _c in _CANDIDATES:
    if os.path.exists(os.path.join(_c, "train_curated.py")):
        sys.path.insert(0, _c)
        break

import train_curated as tc


def test_core_feature_lists_have_expected_sizes():
    assert len(tc.CORE_FEATURES) == 48
    assert len(tc.SENTIMENT_NEWS_FEATURES) == 5
    assert len(set(tc.CORE_FEATURES)) == 48
    assert set(tc.SENTIMENT_NEWS_FEATURES).issubset(set(tc.CORE_FEATURES))


def test_select_curated_features_default_excludes_sentiment_news():
    feature_names = list(tc.CORE_FEATURES) + ["rsi", "macd", "not_in_core"]
    curated = tc.select_curated_features(feature_names, allow_sentiment=False)

    assert len(curated) == 43
    assert set(curated).isdisjoint(set(tc.SENTIMENT_NEWS_FEATURES))
    assert all(f in feature_names for f in curated)
    assert set(curated).issubset(set(tc.CORE_FEATURES))


def test_select_curated_features_allow_sentiment_includes_all_core():
    feature_names = list(tc.CORE_FEATURES) + ["rsi"]
    curated = tc.select_curated_features(feature_names, allow_sentiment=True)

    assert len(curated) == 48
    assert set(tc.SENTIMENT_NEWS_FEATURES).issubset(set(curated))


def test_select_curated_features_intersection_only():
    feature_names = ["price", "return_5d", "sentiment_avg", "unrelated"]

    curated_default = tc.select_curated_features(feature_names, allow_sentiment=False)
    assert curated_default == ["price", "return_5d"]

    curated_sent = tc.select_curated_features(feature_names, allow_sentiment=True)
    assert curated_sent == ["price", "return_5d", "sentiment_avg"]


def test_aggregate_seeds_mean_and_std():
    aucs = {0: 0.60, 1: 0.62, 2: 0.58}
    mean, std = tc.aggregate_seeds(aucs)

    assert mean == pytest.approx(0.60)
    assert std == pytest.approx(0.02)


def test_aggregate_seeds_single_seed_std_zero():
    mean, std = tc.aggregate_seeds({0: 0.60})
    assert mean == pytest.approx(0.60)
    assert std == 0.0


def test_oversample_balance_makes_classes_equal():
    rng = np.random.default_rng(0)
    X = np.arange(20, dtype=np.float32).reshape(10, 2)
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])  # 2 pos, 8 neg

    Xb, yb = tc.oversample_balance(X, y, rng)

    assert int(yb.sum()) == len(yb) - int(yb.sum())
    assert Xb.shape[1] == X.shape[1]
    assert set(yb.tolist()) == {0, 1}


def test_split_train_val_fraction():
    rng = np.random.default_rng(0)
    X = rng.random((100, 5)).astype(np.float32)
    y = rng.integers(0, 2, 100).astype(int)

    Xt, yt, Xv, yv = tc.split_train_val(X, y, 0.67)

    assert len(Xt) == 67
    assert len(Xv) == 33
    assert len(yt) == 67
    assert len(yv) == 33


def test_build_result_dict_schema():
    meta = tc.build_result_dict(
        ensemble_auc=0.6012,
        model_aucs={"xgboost": 0.51, "lightgbm": 0.60, "catboost": 0.50},
        n_rows=1200,
        n_features=43,
        up_rate=0.46,
        seeds=[0, 1, 2],
        seed_aucs={0: 0.60, 1: 0.61, 2: 0.59},
        auc_mean=0.60,
        auc_std=0.01,
        config="curated lr=0.03 depth=4 est=1500",
    )

    required = {
        "ensemble_auc", "model_aucs", "n_rows", "n_features",
        "up_rate", "seeds", "seed_aucs", "auc_mean", "auc_std",
        "retrained_at", "config",
    }
    assert required.issubset(set(meta.keys()))

    assert meta["ensemble_auc"] == pytest.approx(0.6012)
    assert meta["n_rows"] == 1200
    assert meta["n_features"] == 43
    assert meta["seeds"] == [0, 1, 2]
    assert set(meta["model_aucs"].keys()) == {"xgboost", "lightgbm", "catboost"}

    # JSON 직렬화 가능성 (champion_promote 가 파일로 읽음)
    json.dumps(meta)
