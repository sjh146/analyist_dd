"""DB-free unit tests for SNS lag features (Phase D, sns_lag_features).

Covers:
 1. 합성 리드/래그 복원: SNS 가격 리드/가격 리드 시프트를 교차상관이 복원.
 2. 데이터 부족 시 기본값(corr 0.0 / best_lag 0).
 3. compute_for_many: 종목별 dict 반환, 키 존재, 유한값.
"""
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "services", "xgboost-ml"))
from app.feature_engine.sns_lag_features import SnsLagFeatures  # noqa: E402


@pytest.fixture
def lag():
    return SnsLagFeatures()


def _panel(shift, seed, n=80):
    """합성 데이터: 수익률 ret + SNS 감정 시프트(shift) 상관.

    shift > 0: SNS 가격 리드(가격 n일 후 반응) → best_lag ≈ +shift.
    shift < 0: 가격 SNS 리드 → best_lag ≈ shift.
    """
    rng = np.random.default_rng(seed)
    dates = [date(2026, 5, 1) + timedelta(days=i) for i in range(n)]
    ret = rng.normal(0.0, 0.015, n)
    # f[t] = ret[t + shift] 에 노이즈 — shift 부호에 따라 리드/래그 결정.
    f = np.roll(ret, -shift) + 0.25 * rng.normal(0, 0.015, n)
    sns = pd.DataFrame({"trade_date": dates, "sentiment_score": np.clip(f, -1, 1)})
    price = pd.DataFrame({"trade_date": dates,
                          "close": 100.0 * np.cumprod(1 + ret)})
    return sns, price


@pytest.fixture
def lag_engine():
    return SnsLagFeatures()


def test_sns_lead_price_recovered(lag_engine):
    sns, price = _panel(shift=2, seed=11)
    res = lag_engine.compute_for_stock(sns, price)
    assert res["sns_sentiment_score_best_lag"] > 0
    assert res["sns_sentiment_score_lag_sign"] == -1  # SNS 리드
    assert res["sns_sentiment_score_max_corr"] > 0.5


def test_price_lead_sns_recovered(lag_engine):
    sns, price = _panel(shift=-3, seed=12)
    res = lag_engine.compute_for_stock(sns, price)
    assert res["sns_sentiment_score_best_lag"] < 0
    assert res["sns_sentiment_score_lag_sign"] == 1  # 가격 리드
    assert res["sns_sentiment_score_max_corr"] > 0.5


def test_lag_zero_prediction(lag_engine):
    # 임의 감정(가격 무관) → 신호 없음, 유한값, sign 동작.
    rng = np.random.default_rng(5)
    n = 60
    dates = [date(2026, 6, 1) + timedelta(days=i) for i in range(n)]
    ret = rng.normal(0, 0.01, n)
    noise = rng.normal(0, 0.01, n)
    sns = pd.DataFrame({"trade_date": dates, "sentiment_score": noise})
    price = pd.DataFrame({"trade_date": dates, "close": 100.0 * np.cumprod(1 + ret)})
    res = lag_engine.compute_for_stock(sns, price)
    assert np.isfinite(res["sns_sentiment_score_max_corr"])
    assert res["sns_sentiment_score_best_lag"] in range(-5, 6)


def test_insufficient_data_defaults(lag_engine):
    # 데이터가 너무 적으면 기본값 (corr 0.0, best_lag 0, sign 0).
    dates = [date(2026, 6, 1), date(2026, 6, 2)]
    sns = pd.DataFrame({"trade_date": dates, "sentiment_score": [0.2, 0.3]})
    price = pd.DataFrame({"trade_date": dates, "close": [100.0, 101.0]})
    res = lag_engine.compute_for_stock(sns, price)
    assert res["sns_sentiment_score_best_lag"] == 0
    assert res["sns_sentiment_score_max_corr"] == 0.0
    assert res["sns_sentiment_score_lag_sign"] == 0


def test_missing_feature_column_defaults(lag_engine):
    dates = [date(2026, 6, 1) + timedelta(days=i) for i in range(30)]
    sns = pd.DataFrame({"trade_date": dates})  # sentiment_score 컬럼 없음
    price = pd.DataFrame({"trade_date": dates, "close": [100.0 + i for i in range(30)]})
    res = lag_engine.compute_for_stock(sns, price)
    assert res["sns_sentiment_score_best_lag"] == 0
    assert res["sns_sentiment_score_max_corr"] == 0.0


def test_all_feature_keys_present(lag_engine):
    rng = np.random.default_rng(7)
    n = 50
    dates = [date(2026, 6, 1) + timedelta(days=i) for i in range(n)]
    ret = rng.normal(0, 0.01, n)
    sns = pd.DataFrame({
        "trade_date": dates,
        "sentiment_score": rng.normal(0, 0.1, n),
        "attention_score": rng.uniform(0, 1, n),
        "momentum_score": rng.uniform(-1, 1, n),
        "author_quality_score": rng.uniform(0, 1, n),
    })
    price = pd.DataFrame({"trade_date": dates, "close": 100.0 * np.cumprod(1 + ret)})
    res = lag_engine.compute_for_stock(sns, price)
    for f in ("sentiment_score", "attention_score", "momentum_score", "author_quality_score"):
        assert f"sns_{f}_best_lag" in res
        assert f"sns_{f}_max_corr" in res
        assert f"sns_{f}_lag_sign" in res
        assert f"sns_{f}_corr0" in res


def test_compute_for_many_multiple_stocks(lag_engine):
    rng = np.random.default_rng(9)
    n = 40
    dates = [date(2026, 6, 1) + timedelta(days=i) for i in range(n)]
    sns_rows, price_rows = [], []
    for code in ("000001", "000002", "000003"):
        ret = rng.normal(0, 0.01, n)
        for i, d in enumerate(dates):
            sns_rows.append({"stock_code": code, "trade_date": d,
                             "sentiment_score": rng.normal(0, 0.1)})
            price_rows.append({"stock_code": code, "trade_date": d,
                               "close": 100.0 * np.cumprod(1 + ret)[i]})
    sns = pd.DataFrame(sns_rows)
    price = pd.DataFrame(price_rows)
    results = lag_engine.compute_for_many(sns, price)
    assert len(results) == 3
    for r in results:
        assert r["stock_code"] in ("000001", "000002", "000003")
        assert "sns_sentiment_score_best_lag" in r
        assert np.isfinite(r["sns_sentiment_score_max_corr"])


def test_returns_column_accepted(lag_engine):
    rng = np.random.default_rng(13)
    n = 70
    dates = [date(2026, 6, 1) + timedelta(days=i) for i in range(n)]
    ret = rng.normal(0, 0.01, n)
    f = np.roll(ret, -2) + 0.2 * rng.normal(0, 0.01, n)  # SNS 리드 2일
    sns = pd.DataFrame({"trade_date": dates, "sentiment_score": np.clip(f, -1, 1)})
    price = pd.DataFrame({"trade_date": dates, "return": ret})
    res = lag_engine.compute_for_stock(sns, price)
    assert res["sns_sentiment_score_best_lag"] > 0
