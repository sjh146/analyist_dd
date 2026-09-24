"""
SNS Feature Bundle — 학습/추론 파이프라인 연결용 SNS 피처 로더
=============================================================

``feature_pipeline.py`` 를 건드리지 않고 SNS 피처를 붙일 수 있도록, 리더
(``sns_features.SnsFeatures`` / ``sns_lag_features.SnsLagFeatures``)를 호출해
**평평한(flat) 피처 dict** 로 만들어 주는 얇은 어댑터.

왜 별도 모듈인가
----------------
1. 파이프라인 쪽 변경을 import 1줄 + 호출 3줄로 줄인다(동시 편집 충돌 최소화).
2. 리더의 산식을 다시 구현하지 않는다 — 날짜 필터/룩어헤드 차단은 리더가 한다.
3. 종목별 **프리페치 캐시**로 (종목 × 날짜) 반복 호출을 감당한다.
   ``build_training_features`` 는 (종목,날짜)마다 ``build_features`` 를 부르므로
   캐시 없이는 종목·날짜당 쿼리 2회 × 수만 회가 된다. 여기서는 종목당 1회만
   DB 를 읽고 이후는 파이썬 슬라이싱으로 처리한다.

피처 이름 계약 (26개, ``feature_names()`` 가 단일 소스)
------------------------------------------------------
- 감정/관심도 계열 6개 (``sns_`` 접두):
  ``sns_sentiment_score``, ``sns_attention_score``, ``sns_momentum_score``,
  ``sns_author_quality_score``, ``sns_post_count``, ``sns_bot_filtered_count``
- 칼만 계열 4개 (리더 키 그대로 — 기존 ``kalman_momentum_1d`` 등과 충돌 없음):
  ``kalman_sentiment``, ``kalman_attention``, ``kalman_momentum``,
  ``kalman_activity``
- 가격–SNS 시차 계열 16개 (리더 키 그대로):
  ``sns_{sentiment_score|attention_score|momentum_score|author_quality_score}``
  ``_{best_lag|max_corr|lag_sign|corr0}``

주의
----
- SNS 데이터가 없는 (종목,날짜) 는 **키를 만들지 않는다**(``{}`` 반환). 파이프라인은
  기존과 동일하게 결측을 0.0 으로 채운다. 0.0 행을 지어내지 않는다.
- ``date`` 를 주면 룩어헤드가 차단된다(``<= date`` + 트레일링 윈도우). 학습
  패널에는 **반드시** ``date`` 를 넘겨야 한다.
- fail-open: DB/쿼리 오류 시 ``{}`` (파이프라인을 죽이지 않는다).
"""

from __future__ import annotations

import logging
from datetime import date as _date
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from app.feature_engine.market_data_filter import MARKET_DATA_VALID
from app.feature_engine.sns_features import SnsFeatures
from app.feature_engine.sns_lag_features import SnsLagFeatures

logger = logging.getLogger(__name__)

#: ``sns_`` 접두를 붙일 리더 키 (칼만 계열은 이미 의미가 분명해 그대로 쓴다).
_SNS_PREFIXED_KEYS = (
    "sentiment_score",
    "attention_score",
    "momentum_score",
    "author_quality_score",
    "post_count",
    "bot_filtered_count",
)
#: 리더가 그대로 반환하는 칼만 키.
_KALMAN_KEYS = (
    "kalman_sentiment",
    "kalman_attention",
    "kalman_momentum",
    "kalman_activity",
)
_LAG_FEATURES = ("sentiment_score", "attention_score", "momentum_score",
                 "author_quality_score")
_LAG_SUFFIXES = ("best_lag", "max_corr", "lag_sign", "corr0")

POSTS_SQL = """
SELECT posted_at::date AS trade_date, text, author_id, author_followers,
       comment_count, like_count, retweet_count, source
FROM sns_posts
WHERE stock_code = %s AND posted_at IS NOT NULL
ORDER BY posted_at
"""

FEATURES_SQL = """
SELECT trade_date, sentiment_score, attention_score, momentum_score,
       author_quality_score, post_count, bot_filtered_count,
       kalman_sentiment, kalman_attention, kalman_momentum, kalman_activity
FROM sns_post_features
WHERE stock_code = %s
ORDER BY trade_date
"""
_TABLE_COLS = ("sentiment_score", "attention_score", "momentum_score",
               "author_quality_score", "post_count", "bot_filtered_count",
               "kalman_sentiment", "kalman_attention", "kalman_momentum",
               "kalman_activity")


def feature_names(with_lag: bool = True) -> List[str]:
    """파이프라인이 기대해야 하는 SNS 피처 이름 전체(정렬된 리스트)."""
    names = [f"sns_{k}" for k in _SNS_PREFIXED_KEYS] + list(_KALMAN_KEYS)
    if with_lag:
        names += [f"sns_{f}_{s}" for f in _LAG_FEATURES for s in _LAG_SUFFIXES]
    return sorted(names)


def _as_date(value) -> Optional[_date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    return _date.fromisoformat(str(value)[:10])


class SnsFeatureBundle:
    """종목별 프리페치 캐시를 가진 SNS 피처 로더.

    Examples
    --------
    >>> b = SnsFeatureBundle(pg_conn)
    >>> b.load("005930", date="2026-09-23")
    {'sns_attention_score': 0.6, ..., 'sns_sentiment_score_corr0': -0.72}
    """

    def __init__(self, pg_conn=None, use_lag: bool = True,
                 window_days: Optional[int] = None, max_posts_per_stock: int = 20000,
                 source: str = "table"):
        self.pg_conn = pg_conn
        self.use_lag = use_lag
        self.window_days = window_days
        self.max_posts_per_stock = max_posts_per_stock
        #: ``table`` = ``sns_post_features`` 값을 그대로 읽는다(빠름, 기본).
        #: ``window`` = 리더로 매 호출 재계산(느림, 30일 트레일링 윈도우).
        self.source = source
        self._sns = SnsFeatures()
        self._lag = SnsLagFeatures()
        self._cache: Dict[str, Dict] = {}

    # ── 캐시 ────────────────────────────────────────────────────────────
    def clear_cache(self, stock_code: Optional[str] = None) -> None:
        if stock_code is None:
            self._cache.clear()
        else:
            self._cache.pop(stock_code, None)

    def _prefetch(self, stock_code: str) -> Dict:
        """종목 1회 DB 조회 → (게시글 전체, SNS 피처 시계열, 가격 시계열)."""
        if stock_code in self._cache:
            return self._cache[stock_code]
        data = {"posts": pd.DataFrame(), "sns": pd.DataFrame(), "price": pd.DataFrame()}
        if self.pg_conn is not None:
            try:
                cur = self.pg_conn.cursor()
                cur.execute(POSTS_SQL, (stock_code,))
                rows = cur.fetchall()
                if rows:
                    data["posts"] = pd.DataFrame(
                        rows,
                        columns=["trade_date", "text", "author_id",
                                 "author_followers", "comment_count",
                                 "like_count", "retweet_count", "source"],
                    )
                    data["posts"]["stock_code"] = stock_code
                    if len(data["posts"]) > self.max_posts_per_stock:
                        data["posts"] = data["posts"].tail(self.max_posts_per_stock)

                cur.execute(FEATURES_SQL, (stock_code,))
                srows = cur.fetchall()
                if srows:
                    data["sns"] = pd.DataFrame(
                        srows, columns=["trade_date", *_TABLE_COLS]
                    )
                    data["sns"]["trade_date"] = [
                        _as_date(v) for v in data["sns"]["trade_date"]
                    ]
                    for f in _TABLE_COLS:
                        data["sns"][f] = pd.to_numeric(
                            data["sns"][f], errors="coerce"
                        ).fillna(0.0)

                if self.use_lag:
                    cur.execute(
                        f"""
                        SELECT trade_date, close_price
                        FROM market_data
                        WHERE stock_code = %s AND {MARKET_DATA_VALID}
                        ORDER BY trade_date DESC
                        LIMIT 400
                        """,
                        (stock_code,),
                    )
                    prows = cur.fetchall()
                    if prows:
                        data["price"] = pd.DataFrame(
                            list(reversed(prows)), columns=["trade_date", "close"]
                        )
                cur.close()
            except Exception as e:  # fail-open
                logger.debug("SNS prefetch 실패 %s: %s", stock_code, e)
                try:
                    self.pg_conn.rollback()
                except Exception:
                    pass
        self._cache[stock_code] = data
        return data

    # ── 공개 API ────────────────────────────────────────────────────────
    def load(self, stock_code: str, date=None) -> Dict[str, float]:
        """(종목, 날짜)의 SNS 피처 dict. 데이터가 없으면 ``{}``."""
        if not stock_code:
            return {}
        target = _as_date(date)
        data = self._prefetch(stock_code)
        out: Dict[str, float] = {}

        # 1) 감정/관심도/칼만 10개.
        #    table  : sns_post_features 의 (종목,일) 행을 그대로 읽는다(O(1)).
        #    window : 리더의 날짜 기준 윈도우 계산(<= date + 30일 트레일링).
        #    테이블에 그 날짜 행이 없으면 window 로 폴백.
        used_table = False
        if self.source == "table" and target is not None and not data["sns"].empty:
            hit = data["sns"][data["sns"]["trade_date"] == target]
            if len(hit):
                row = hit.iloc[0]
                for k in _TABLE_COLS:
                    name = k if k.startswith("kalman_") else f"sns_{k}"
                    out[name] = float(row[k])
                used_table = True
        if not used_table and not data["posts"].empty:
            rows = self._sns.get_daily_features(
                stock_code, data["posts"], date=target,
                window_days=self.window_days,
            )
            if rows:
                row = rows[0]
                for k in _SNS_PREFIXED_KEYS:
                    out[f"sns_{k}"] = float(row[k])
                for k in _KALMAN_KEYS:
                    out[k] = float(row[k])

        # 2) 가격–SNS 시차 — 리더 SQL 과 동일한 슬라이스(<= date, 최근 120행).
        if self.use_lag and not data["sns"].empty and not data["price"].empty:
            sns_df = data["sns"][["trade_date", *_LAG_FEATURES]]
            price_df = data["price"]
            if target is not None:
                sns_df = sns_df[
                    [d is not None and d <= target for d in sns_df["trade_date"]]
                ].tail(self._lag.LOOKBACK_ROWS)
                price_df = price_df[
                    [d is not None and _as_date(d) <= target for d in price_df["trade_date"]]
                ].tail(self._lag.LOOKBACK_ROWS)
            if not sns_df.empty and not price_df.empty:
                res = self._lag.compute_for_stock(sns_df, price_df)
                for k, v in res.items():
                    if k == "stock_code":
                        continue
                    out[k] = float(v)

        return out
