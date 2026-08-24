"""
SNS Features (Phase B of the SNS intelligence pipeline)
========================================================

Computes 4 per-stock, per-day features from SNS posts:

- ``sentiment_score``      : rule-based Korean finance lexicon sentiment in [-1, 1].
- ``attention_score``      : bounded [0, 1] engagement/volume attention.
- ``momentum_score``       : SNS activity trend/acceleration in [-1, 1].
- ``author_quality_score`` : per-post author quality in [0, 1] (followers,
  engagement, bot/repeat penalty, polarity bias).

Plus a Kalman-based bot/spam filter that denoises the daily activity series and
flags residual spikes / periodic (auto-correlated) patterns as bot-like.

Design
------
The core transforms operate on **in-memory** data (a list of post dicts or a
per-stock DataFrame) so unit tests run with NO database. A DB-backed
``compute_for_stock`` convenience queries ``sns_posts`` aggregated by day and
returns daily feature rows, defaulting to 0.0 when ``db_conn`` is None or the
query fails (defensive, fail-open style — mirrors ``news_event_features.py``).

Post dict / DataFrame columns
-----------------------------
``stock_code, trade_date, text, author_id, author_followers, comment_count,
like_count, retweet_count, source``
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.feature_engine.kalman_smoother import KalmanSmoother

logger = logging.getLogger(__name__)

# 작은 상수 — 0 나눗셈 방지용 epsilon.
_EPS = 1e-8


class SnsFeatures:
    """Per-stock, per-day SNS feature computation with a Kalman bot filter."""

    # ── 한국 금융 감정 사전 (1차 규칙 기반) ─────────────────────────────
    # 긍정 키워드: 상승/호재 계열.
    POSITIVE_KW = {
        "상승", "실적", "흑자", "성장", "신기록", "호재", "반등", "추가상승",
        "신고가", "대박", "급등", "강세", "매수", "목표가", "상향", "돌파",
        "선전", "호황", "수혜", "기대", "개선", "증가", "확대", "호실적",
        "어닝서프라이즈", "깜짝실적", "최고", "역대급", "훈풍", "낙관",
        "매출증가", "이익증가", "수주", "신제품", "특허", "수출호조",
        "주가상승", "상한가", "급반등", "저평가", "매력", "유망", "강한",
        "긍정", "호전", "회복", "상승세", "우상향", "기대감", "모멘텀",
    }
    # 부정 키워드: 하락/악재 계열.
    NEGATIVE_KW = {
        "손실", "하락", "악재", "부도", "적자", "대출", "추락", "폭락", "감산",
        "수요부진", "염려", "급락", "약세", "매도", "목표가하향", "하향",
        "붕괴", "침체", "우려", "악화", "감소", "축소", "부진", "실적악화",
        "적자전환", "어닝쇼크", "충격", "최저", "역대급하락", "한파", "비관",
        "매출감소", "이익감소", "수주감소", "리콜", "소송", "규제", "거래정지",
        "상폐", "주가하락", "하한가", "급추락", "고평가", "위험", "부정",
        "악화", "침체", "하락세", "우하향", "불안", "리스크", "경고",
    }

    # ── 파라미터 ────────────────────────────────────────────────────────
    # attention 정규화용 윈도우 (해당 종목 최근 N일 중앙값).
    ATTENTION_WINDOW_DAYS = 7
    # momentum: 최근 활동 기간 / 그 이전 기간.
    RECENT_DAYS = 5
    PREV_DAYS = 5
    # author_quality: 반복/봇 의심 임계값.
    REPEAT_THRESHOLD = 5      # 같은 author_id 하루 게시글 수 초과 시 봇 의심.
    DUP_THRESHOLD = 3         # 같은 텍스트 하루 반복 수 초과 시 봇 의심.
    # Kalman bot/spam 필터.
    RESID_Z_THRESHOLD = 2.5   # 잔차 |obs - smoothed| > z * noise_resid_std → 플래그.
    LAG7_CORR_THRESHOLD = 0.7 # lag-7 자기상관 > 임계값 → 주기적 봇 패턴.
    KALMAN_WINDOW_DAYS = 30   # 활동 시계열 윈도우.
    BOT_REMOVAL_FRACTION = 0.8  # 플래그된 날 제거 비율 (bot_filtered_count 계산용).

    def __init__(self) -> None:
        self._smoother = KalmanSmoother()

    # ════════════════════════════════════════════════════════════════════
    # 1. sentiment_score
    # ════════════════════════════════════════════════════════════════════
    def post_sentiment(self, text: str) -> float:
        """단일 게시글 텍스트의 규칙 기반 감정 점수 ([-1, 1]).

        긍정/부정 키워드 히트 수 차이를 합으로 나누고 tanh로 (-1, 1)에
        압축한다. 키워드 히트가 없으면 중립 0.0 (모호한 글은 Phase C의
        DeepSeek가 처리).
        """
        if not text:
            return 0.0
        pos = sum(1 for kw in self.POSITIVE_KW if kw in text)
        neg = sum(1 for kw in self.NEGATIVE_KW if kw in text)
        if pos + neg == 0:
            return 0.0
        raw = (pos - neg) / (pos + neg + _EPS)
        return float(np.tanh(raw))

    def daily_sentiment(self, posts: List[Dict]) -> float:
        """일별 감정 = 게시글 감정 점수의 평균 (게시글 없으면 0.0)."""
        if not posts:
            return 0.0
        scores = [self.post_sentiment(p.get("text", "")) for p in posts]
        return float(np.mean(scores))

    # ════════════════════════════════════════════════════════════════════
    # 2. attention_score
    # ════════════════════════════════════════════════════════════════════
    def _post_attention(self, post: Dict) -> float:
        """단일 게시글의 원시 주의도 = 1 + 댓글 + 2*좋아요 + 3*리트윗."""
        return (
            1.0
            + float(post.get("comment_count", 0) or 0)
            + 2.0 * float(post.get("like_count", 0) or 0)
            + 3.0 * float(post.get("retweet_count", 0) or 0)
        )

    def daily_attention(self, posts: List[Dict], baseline: float = 0.0) -> float:
        """일별 주의도 (>= 0), [0, 1]로 정규화.

        att = sum(게시글 주의도). 정규화: att / (att + baseline + eps).
        baseline은 해당 종목 최근 윈도우의 중앙값(기본 0 → att만으로 정규화).
        """
        att = sum(self._post_attention(p) for p in posts)
        return float(att / (att + baseline + _EPS))

    def _attention_baseline(self, daily_counts: Dict[date, float]) -> float:
        """최근 ATTENTION_WINDOW_DAYS 일의 일별 주의도 중앙값 (기본 0)."""
        if not daily_counts:
            return 0.0
        vals = list(daily_counts.values())
        window = vals[-self.ATTENTION_WINDOW_DAYS:]
        return float(np.median(window)) if window else 0.0

    # ════════════════════════════════════════════════════════════════════
    # 3. momentum_score
    # ════════════════════════════════════════════════════════════════════
    def momentum_score(self, activity_recent: float, activity_prev: float) -> float:
        """SNS 활동 모멘텀 ([-1, 1]).

        momentum = (recent - prev) / (recent + prev + eps), tanh 압축.
        기준(prev)이 없으면 0.0.
        """
        if activity_recent + activity_prev <= 0:
            return 0.0
        raw = (activity_recent - activity_prev) / (
            activity_recent + activity_prev + _EPS
        )
        return float(np.tanh(raw))

    # ════════════════════════════════════════════════════════════════════
    # 4. author_quality_score
    # ════════════════════════════════════════════════════════════════════
    def post_author_quality(
        self,
        post: Dict,
        author_post_count: int = 1,
        author_dup_count: int = 0,
        author_polarity_bias: float = 0.0,
    ) -> float:
        """단일 게시글의 작성자 품질 점수 ([0, 1]).

        - follower score : 1 - 1/(1 + log1p(followers)) — 팔로워 많을수록 높음.
        - engagement     : 1 - exp(-(댓글+좋아요+리트윗)/eps2).
        - 봇/반복 패널티  : 같은 author_id가 하루 REPEAT_THRESHOLD 초과 게시,
          또는 같은 텍스트가 DUP_THRESHOLD 초과 반복 → 품질 하락.
        - 극성 편향 패널티: 항상 긍정/항상 부정(모든 글 같은 부호) → 소폭 감점.
        """
        followers = float(post.get("author_followers", 0) or 0)
        follower_score = 1.0 - 1.0 / (1.0 + np.log1p(followers) + _EPS)

        engagement_raw = (
            float(post.get("comment_count", 0) or 0)
            + float(post.get("like_count", 0) or 0)
            + float(post.get("retweet_count", 0) or 0)
        )
        engagement = 1.0 - np.exp(-engagement_raw / 5.0)

        # 봇/반복 패널티: 임계값 초과분에 비례해 감점.
        bot_penalty = 0.0
        if author_post_count > self.REPEAT_THRESHOLD:
            bot_penalty += 0.3 * min(
                1.0, (author_post_count - self.REPEAT_THRESHOLD) / self.REPEAT_THRESHOLD
            )
        if author_dup_count > self.DUP_THRESHOLD:
            bot_penalty += 0.3 * min(
                1.0, (author_dup_count - self.DUP_THRESHOLD) / self.DUP_THRESHOLD
            )

        # 극성 편향 패널티: |bias| 가 1에 가까울수록 감점.
        polarity_penalty = 0.1 * abs(author_polarity_bias)

        score = 0.5 * follower_score + 0.5 * engagement
        score -= bot_penalty
        score -= polarity_penalty
        return float(min(max(score, 0.0), 1.0))

    def daily_author_quality(self, posts: List[Dict]) -> float:
        """일별 작성자 품질 = 게시글 품질 평균 ([0, 1], 게시글 없으면 0.0)."""
        if not posts:
            return 0.0
        # 작성자별 하루 게시 수 / 중복 텍스트 수 / 극성 편향 집계.
        author_counts: Dict[str, int] = {}
        dup_counts: Dict[str, int] = {}
        author_signs: Dict[str, List[float]] = {}
        for p in posts:
            aid = str(p.get("author_id", ""))
            author_counts[aid] = author_counts.get(aid, 0) + 1
            text = str(p.get("text", ""))
            dup_counts[text] = dup_counts.get(text, 0) + 1
            s = self.post_sentiment(text)
            if s != 0.0:
                author_signs.setdefault(aid, []).append(s)

        scores = []
        for p in posts:
            aid = str(p.get("author_id", ""))
            text = str(p.get("text", ""))
            signs = author_signs.get(aid, [])
            bias = float(np.mean(signs)) if signs else 0.0
            scores.append(
                self.post_author_quality(
                    p,
                    author_post_count=author_counts.get(aid, 1),
                    author_dup_count=dup_counts.get(text, 0),
                    author_polarity_bias=bias,
                )
            )
        return float(np.mean(scores))

    # ════════════════════════════════════════════════════════════════════
    # 5. Kalman bot/spam filter
    # ════════════════════════════════════════════════════════════════════
    def compute_kalman(self, activity_series: List[float]) -> Dict:
        """활동 시계열에 Kalman smoother를 적용해 잔차/추세를 반환.

        활동값은 0이 될 수 있는 카운트이므로 ``log1p(counts)``를 스무딩한다
        (KalmanSmoother.smooth는 양수 가격의 로그수익률을 내부 계산).
        시계열이 너무 짧으면 KalmanSmoother가 중립 dict를 반환하므로 그대로
        통과시킨다.

        Returns
        -------
        dict
            ``smoothed``, ``observations``, ``trend``, ``slope``,
            ``noise_resid_std``, ``n_obs`` (KalmanSmoother.smooth 출력).
        """
        if not activity_series:
            return {
                "smoothed": np.array([], dtype=float),
                "observations": np.array([], dtype=float),
                "trend": 0.0,
                "slope": 0.0,
                "noise_resid_std": 0.0,
                "n_obs": 0,
            }
        # log1p로 0 포함 카운트를 양수로 변환해 스무딩.
        log_counts = [float(np.log1p(max(c, 0.0))) for c in activity_series]
        return self._smoother.smooth(log_counts)

    def filter_bot_spam(self, activity_series: List[float]) -> Dict:
        """활동 시계열에서 봇/스팸 날짜를 플래그한다.

        - 잔차 기반: |observed - smoothed| > RESID_Z_THRESHOLD * noise_resid_std
          인 날을 봇/스팸으로 플래그.
        - 주기 기반: lag-7 자기상관 > LAG7_CORR_THRESHOLD 이면 주기적 봇 패턴.

        Returns
        -------
        dict
            ``flagged_days`` : list[int] — 플래그된 인덱스.
            ``bot_filtered_count`` : float — 제거된 게시글 수(플래그된 날의
                카운트 합 * BOT_REMOVAL_FRACTION).
            ``cleaned_series`` : list[float] — 플래그된 날을 0으로 만든 시계열.
            ``kalman`` : dict — compute_kalman 출력.
        """
        n = len(activity_series)
        if n == 0:
            return {
                "flagged_days": [],
                "bot_filtered_count": 0.0,
                "cleaned_series": [],
                "kalman": self.compute_kalman([]),
            }

        kalman = self.compute_kalman(activity_series)
        smoothed = kalman.get("smoothed", np.array([]))
        noise_std = float(kalman.get("noise_resid_std", 0.0) or 0.0)

        flagged: List[int] = []
        # 잔차 기반 플래그 (로그수익률 공간 정렬).
        #
        # KalmanSmoother.smooth 는 내부에서 로그수익률(np.diff)을 계산하므로
        # 반환하는 ``smoothed``/``observations`` 는 길이가 n-1 (원 시계열보다
        # 하니 작음) 이다. 관측 로그수익률 k 는 원 시계열 인덱스 k+1 의 전이를
        # 나타내므로, 플래그된 로그수익률 인덱스 k 를 원 인덱스 k+1 로 매핑한다.
        observations = kalman.get("observations", np.array([]))
        if (
            len(smoothed) == len(observations)
            and len(smoothed) > 0
            and noise_std > 0
        ):
            resid = np.abs(np.asarray(observations) - np.asarray(smoothed))
            threshold = self.RESID_Z_THRESHOLD * noise_std
            flagged = sorted({int(k) + 1 for k, r in enumerate(resid) if r > threshold})

        # 주기 기반 플래그: lag-7 자기상관.
        if n > 14:
            arr = np.asarray(activity_series, dtype=float)
            corr = self._lag_corr(arr, 7)
            if corr is not None and corr > self.LAG7_CORR_THRESHOLD:
                # 주기적 봇 패턴 → 가장 큰 스파이크 날짜를 플래그.
                spike = int(np.argmax(arr))
                if spike not in flagged:
                    flagged.append(spike)

        # 제거된 게시글 수: 플래그된 날의 카운트 합 * 제거 비율.
        removed = sum(activity_series[i] for i in flagged) * self.BOT_REMOVAL_FRACTION

        cleaned = list(activity_series)
        for i in flagged:
            cleaned[i] = 0.0

        return {
            "flagged_days": sorted(flagged),
            "bot_filtered_count": float(removed),
            "cleaned_series": cleaned,
            "kalman": kalman,
        }

    @staticmethod
    def _lag_corr(arr: np.ndarray, lag: int) -> Optional[float]:
        """시계열의 lag 자기상관 (데이터 부족 시 None)."""
        if len(arr) <= lag:
            return None
        x = arr[:-lag]
        y = arr[lag:]
        if np.std(x) == 0 or np.std(y) == 0:
            return None
        return float(np.corrcoef(x, y)[0, 1])

    # ════════════════════════════════════════════════════════════════════
    # 6. get_daily_features
    # ════════════════════════════════════════════════════════════════════
    def _posts_to_daily(self, posts_df: pd.DataFrame) -> Dict[date, List[Dict]]:
        """DataFrame을 (trade_date -> 게시글 dict 목록)으로 그룹화."""
        daily: Dict[date, List[Dict]] = {}
        for _, row in posts_df.iterrows():
            d = row.get("trade_date")
            if d is None:
                continue
            if isinstance(d, (datetime, pd.Timestamp)):
                d = d.date()
            elif isinstance(d, str):
                d = date.fromisoformat(str(d)[:10])
            daily.setdefault(d, []).append(row.to_dict())
        return daily

    def get_daily_features(
        self, stock_code: str, posts_df: pd.DataFrame, db_conn=None
    ) -> List[Dict]:
        """종목별 일별 SNS 피처 행 목록을 반환한다.

        ``posts_df`` 는 (stock_code, trade_date, text, author_id,
        author_followers, comment_count, like_count, retweet_count, source)
        컬럼을 가진 DataFrame. DB는 사용하지 않는다 (db_conn은 호환용).

        Returns
        -------
        list[dict]
            날짜 오름차순의 일별 행. 키는 ``sns_post_features`` SQL 컬럼과
            일치: stock_code, trade_date, sentiment_score, attention_score,
            momentum_score, author_quality_score, post_count,
            bot_filtered_count, kalman_sentiment, kalman_attention,
            kalman_momentum, kalman_activity.
        """
        if posts_df is None or posts_df.empty:
            return []

        daily = self._posts_to_daily(posts_df)
        if not daily:
            return []

        dates = sorted(daily.keys())

        # 일별 활동 카운트 시계열 (Kalman 필터용).
        activity_series = [float(len(daily[d])) for d in dates]
        bot = self.filter_bot_spam(activity_series)
        kalman = bot["kalman"]
        cleaned_series = bot["cleaned_series"]
        flagged = set(bot["flagged_days"])

        # 일별 주의도 시계열 (attention baseline 계산용).
        daily_att = {
            d: sum(self._post_attention(p) for p in daily[d]) for d in dates
        }
        baseline = self._attention_baseline(daily_att)

        # Kalman 스무딩된 감정/주의도/모멘텀/활동 (log1p 공간).
        kalman_smoothed = kalman.get("smoothed", np.array([]))
        kalman_obs = kalman.get("observations", np.array([]))

        rows: List[Dict] = []
        for idx, d in enumerate(dates):
            posts = daily[d]
            post_count = len(posts)

            sentiment = self.daily_sentiment(posts)
            attention = self.daily_attention(posts, baseline)
            author_quality = self.daily_author_quality(posts)

            # 모멘텀: 최근 RECENT_DAYS vs 그 이전 PREV_DAYS (필터된 활동).
            recent = sum(cleaned_series[max(0, idx - self.RECENT_DAYS + 1): idx + 1])
            prev = sum(cleaned_series[max(0, idx - self.RECENT_DAYS - self.PREV_DAYS + 1): max(0, idx - self.RECENT_DAYS + 1)])
            momentum = self.momentum_score(recent, prev)

            # Kalman 스무딩된 값 (log1p 공간 → 역변환해 원래 스케일 근사).
            kalman_activity = 0.0
            if idx < len(kalman_smoothed):
                kalman_activity = float(np.expm1(kalman_smoothed[idx]))
            kalman_sentiment = sentiment
            kalman_attention = attention
            kalman_momentum = momentum
            if idx < len(kalman_obs):
                # 관측된 활동의 스무딩된 감정/주의도/모멘텀 근사.
                obs_act = float(np.expm1(kalman_obs[idx]))
                kalman_attention = self.daily_attention(posts, baseline)
                kalman_momentum = self.momentum_score(obs_act, prev)

            # 봇 필터링된 게시글 수 (이 날이 플래그되면 제거분 반영).
            bot_filtered_count = 0.0
            if idx in flagged:
                bot_filtered_count = float(post_count) * self.BOT_REMOVAL_FRACTION

            rows.append(
                {
                    "stock_code": stock_code,
                    "trade_date": d,
                    "sentiment_score": float(sentiment),
                    "attention_score": float(attention),
                    "momentum_score": float(momentum),
                    "author_quality_score": float(author_quality),
                    "post_count": int(post_count),
                    "bot_filtered_count": float(bot_filtered_count),
                    "kalman_sentiment": float(kalman_sentiment),
                    "kalman_attention": float(kalman_attention),
                    "kalman_momentum": float(kalman_momentum),
                    "kalman_activity": float(kalman_activity),
                }
            )
        return rows

    # ════════════════════════════════════════════════════════════════════
    # DB-backed convenience
    # ════════════════════════════════════════════════════════════════════
    def compute_for_stock(self, stock_code: str, db_conn=None) -> List[Dict]:
        """``sns_posts`` 를 일별 집계해 종목의 일별 피처 행을 반환한다.

        ``db_conn`` 이 None이거나 쿼리가 실패하면 빈 리스트를 반환한다
        (fail-open). 실제 집계는 ``get_daily_features`` 로 위임한다.
        """
        if db_conn is None:
            return []

        try:
            cur = db_conn.cursor()
            cur.execute(
                """
                SELECT trade_date, text, author_id, author_followers,
                       comment_count, like_count, retweet_count, source
                FROM sns_posts
                WHERE stock_code = %s
                ORDER BY trade_date
                """,
                (stock_code,),
            )
            rows = cur.fetchall()
            cols = [
                "trade_date", "text", "author_id", "author_followers",
                "comment_count", "like_count", "retweet_count", "source",
            ]
            cur.close()
            if not rows:
                return []
            df = pd.DataFrame(rows, columns=cols)
            df["stock_code"] = stock_code
            return self.get_daily_features(stock_code, df)
        except Exception as e:
            logger.debug("sns features query failed for %s: %s", stock_code, e)
            if db_conn:
                try:
                    db_conn.rollback()
                except Exception:
                    pass
            return []
