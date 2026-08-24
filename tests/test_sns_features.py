"""DB-free unit tests for the SNS feature engine (Phase B).

Covers:
 1. sentiment: all-positive / all-negative / mixed / no-keyword, bounds [-1,1].
 2. attention: more posts/comments/likes -> strictly higher; bounded [0,1].
 3. momentum: rising activity -> positive; falling -> negative.
 4. author_quality: high-follower+engagement near 1; spammy/repeating penalized.
 5. Kalman bot filter: synthetic burst spike flagged; flat series few/no flags.
 6. get_daily_features: all expected column keys, in-bounds values.
"""
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "xgboost-ml"))

from app.feature_engine.sns_features import SnsFeatures  # noqa: E402


@pytest.fixture
def sf():
    return SnsFeatures()


def _post(text="", followers=0, comments=0, likes=0, retweets=0, author_id="a",
          trade_day=date(2026, 8, 1)):
    return {
        "stock_code": "005930",
        "trade_date": trade_day,
        "text": text,
        "author_id": author_id,
        "author_followers": followers,
        "comment_count": comments,
        "like_count": likes,
        "retweet_count": retweets,
        "source": "test",
    }


# ── 1. sentiment ───────────────────────────────────────────────────────
def test_sentiment_all_positive(sf):
    posts = [_post(text="실적 상승 흑자 성장 신기록 호재") for _ in range(5)]
    s = sf.daily_sentiment(posts)
    assert s > 0.0
    assert -1.0 <= s <= 1.0


def test_sentiment_all_negative(sf):
    posts = [_post(text="손실 하락 악재 부도 적자 폭락") for _ in range(5)]
    s = sf.daily_sentiment(posts)
    assert s < 0.0
    assert -1.0 <= s <= 1.0


def test_sentiment_mixed(sf):
    pos = sf.post_sentiment("상승 실적 흑자")
    neg = sf.post_sentiment("손실 하락 악재")
    mixed = sf.post_sentiment("상승 손실")
    # 혼합은 순수 긍정/부정 사이.
    assert neg < mixed < pos
    assert -1.0 <= mixed <= 1.0


def test_sentiment_no_keyword_neutral(sf):
    assert sf.post_sentiment("오늘 날씨가 좋네요") == 0.0
    assert sf.post_sentiment("") == 0.0
    assert sf.daily_sentiment([]) == 0.0


# ── 2. attention ───────────────────────────────────────────────────────
def test_attention_more_activity_higher(sf):
    low = sf.daily_attention([_post()])
    more_posts = sf.daily_attention([_post(), _post(), _post()])
    with_comments = sf.daily_attention([_post(comments=10)])
    with_likes = sf.daily_attention([_post(likes=10)])
    with_retweets = sf.daily_attention([_post(retweets=10)])
    assert more_posts > low
    assert with_comments > low
    assert with_likes > with_comments  # 2*likes > comments
    assert with_retweets > with_likes  # 3*retweets > 2*likes
    for v in (low, more_posts, with_comments, with_likes, with_retweets):
        assert 0.0 <= v <= 1.0


def test_attention_bounded(sf):
    big = sf.daily_attention([_post(likes=1000, retweets=1000, comments=1000)])
    assert 0.0 <= big <= 1.0


# ── 3. momentum ────────────────────────────────────────────────────────
def test_momentum_rising_positive(sf):
    assert sf.momentum_score(100.0, 10.0) > 0.0
    assert -1.0 <= sf.momentum_score(100.0, 10.0) <= 1.0


def test_momentum_falling_negative(sf):
    assert sf.momentum_score(10.0, 100.0) < 0.0


def test_momentum_no_baseline_zero(sf):
    assert sf.momentum_score(0.0, 0.0) == 0.0


# ── 4. author_quality ──────────────────────────────────────────────────
def test_author_quality_high_follower_engagement(sf):
    high = sf.post_author_quality(
        _post(followers=100000, comments=50, likes=200, retweets=30)
    )
    assert high > 0.8
    assert 0.0 <= high <= 1.0


def test_author_quality_low_follower_spammy(sf):
    low = sf.post_author_quality(
        _post(followers=0, comments=0, likes=0, retweets=0),
        author_post_count=20,  # REPEAT_THRESHOLD(5) 초과 → 봇 의심.
        author_dup_count=10,   # DUP_THRESHOLD(3) 초과 → 중복.
    )
    assert low < 0.5
    assert 0.0 <= low <= 1.0


def test_author_quality_repeating_author_penalized(sf):
    # 반복 작성자: 같은 author_id가 하루 20개 게시.
    repeating = [_post(author_id="bot", text="매수 상승") for _ in range(20)]
    # 정상 고품질 작성자: 팔로워 많고 참여도 높음.
    normal = [
        _post(author_id="good", followers=50000, comments=30, likes=100,
              retweets=20, text="실적 상승 흑자")
    ]
    q_repeat = sf.daily_author_quality(repeating)
    q_normal = sf.daily_author_quality(normal)
    assert q_repeat < q_normal
    assert 0.0 <= q_repeat <= 1.0
    assert 0.0 <= q_normal <= 1.0


# ── 5. Kalman bot filter ───────────────────────────────────────────────
def test_kalman_flags_burst_spike(sf):
    # 정상 평탄 시계열 + 중간에 스팸 버스트 스파이크.
    series = [5.0] * 15 + [500.0] + [5.0] * 14
    result = sf.filter_bot_spam(series)
    assert len(result["flagged_days"]) >= 1
    assert result["bot_filtered_count"] >= 1.0
    # 스파이크 인덱스(15)가 플래그되어야 함.
    assert 15 in result["flagged_days"]
    # cleaned_series에서 스파이크가 제거됨.
    assert result["cleaned_series"][15] == 0.0


def test_kalman_flat_series_few_flags(sf):
    series = [5.0] * 30
    result = sf.filter_bot_spam(series)
    # 평탄 시리즈는 잔차가 거의 없어 플래그가 없어야 함.
    assert len(result["flagged_days"]) == 0
    assert result["bot_filtered_count"] == 0.0


def test_kalman_outputs_present_finite(sf):
    series = [3.0, 4.0, 5.0, 6.0, 5.0, 7.0, 8.0, 6.0, 5.0, 4.0,
              5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 8.0, 7.0, 6.0, 5.0]
    result = sf.filter_bot_spam(series)
    kalman = result["kalman"]
    for key in ("smoothed", "observations", "trend", "slope",
                "noise_resid_std", "n_obs"):
        assert key in kalman
    assert np.all(np.isfinite(kalman["smoothed"]))
    assert np.all(np.isfinite(kalman["observations"]))
    assert np.isfinite(kalman["trend"])
    assert np.isfinite(kalman["slope"])


def test_kalman_short_series_neutral(sf):
    result = sf.filter_bot_spam([3.0, 4.0])
    assert result["bot_filtered_count"] == 0.0
    assert result["kalman"]["trend"] == 0.0


# ── 6. get_daily_features ──────────────────────────────────────────────
def test_get_daily_features_columns_and_bounds(sf):
    dates = [date(2026, 8, 1) + timedelta(days=i) for i in range(10)]
    rows = []
    for i, d in enumerate(dates):
        rows.append(_post(text="실적 상승", followers=1000, likes=5,
                          comments=2, retweets=1, author_id=f"u{i}", trade_day=d))
        rows.append(_post(text="손실 하락", followers=10, likes=0,
                          comments=0, retweets=0, author_id=f"v{i}", trade_day=d))
    df = pd.DataFrame(rows)
    result = sf.get_daily_features("005930", df)

    assert len(result) == 10
    expected_keys = {
        "stock_code", "trade_date", "sentiment_score", "attention_score",
        "momentum_score", "author_quality_score", "post_count",
        "bot_filtered_count", "kalman_sentiment", "kalman_attention",
        "kalman_momentum", "kalman_activity",
    }
    for row in result:
        assert set(row.keys()) == expected_keys
        assert row["stock_code"] == "005930"
        assert -1.0 <= row["sentiment_score"] <= 1.0
        assert 0.0 <= row["attention_score"] <= 1.0
        assert -1.0 <= row["momentum_score"] <= 1.0
        assert 0.0 <= row["author_quality_score"] <= 1.0
        assert row["post_count"] == 2
        assert row["bot_filtered_count"] >= 0.0
        assert np.isfinite(row["kalman_activity"])
        assert np.isfinite(row["kalman_sentiment"])


def test_get_daily_features_empty(sf):
    assert sf.get_daily_features("005930", pd.DataFrame()) == []
    assert sf.get_daily_features("005930", None) == []


def test_compute_for_stock_no_db(sf):
    assert sf.compute_for_stock("005930", db_conn=None) == []
