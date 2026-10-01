"""
Sentiment Features
Extracts features from sentiment analysis data stored in PostgreSQL.
"""

import logging
from datetime import datetime

import numpy as np
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SentimentFeatures:
    """Features derived from news sentiment and community data."""

    def get_aggregate_sentiment(self, sentiment_data: List[Dict]) -> Dict:
        """Calculate aggregate sentiment features from time-series sentiment data."""
        features = {
            "sentiment_avg": 0.0, "sentiment_avg_5d": 0.0,
            "sentiment_avg_20d": 0.0, "sentiment_trend": 0.0,
            "sentiment_volatility": 0.0,
            "news_count_5d": 0, "news_count_20d": 0,
            "authenticity_avg": 0.0,
            "positive_ratio": 0.0, "negative_ratio": 0.0,
        }

        if not sentiment_data:
            return features

        scores = [s.get("avg_sentiment", 0) for s in sentiment_data]
        counts = [s.get("sentiment_count", 0) for s in sentiment_data]
        pos_counts = [s.get("positive_count", 0) for s in sentiment_data]
        neg_counts = [s.get("negative_count", 0) for s in sentiment_data]
        auth_scores = [s.get("avg_authenticity", 0) for s in sentiment_data]

        features["sentiment_avg"] = float(np.mean(scores)) if scores else 0.0

        if len(scores) >= 5:
            features["sentiment_avg_5d"] = float(np.mean(scores[-5:]))
        else:
            features["sentiment_avg_5d"] = features["sentiment_avg"]

        if len(scores) >= 20:
            features["sentiment_avg_20d"] = float(np.mean(scores[-20:]))
        else:
            features["sentiment_avg_20d"] = features["sentiment_avg"]

        features["sentiment_trend"] = float(scores[-1] - scores[0]) if len(scores) >= 2 else 0.0
        features["sentiment_volatility"] = float(np.std(scores)) if len(scores) > 1 else 0.0

        features["news_count_5d"] = int(np.sum(counts[-5:])) if len(counts) >= 5 else int(np.sum(counts))
        features["news_count_20d"] = int(np.sum(counts[-20:])) if len(counts) >= 20 else int(np.sum(counts))

        features["authenticity_avg"] = float(np.mean(auth_scores)) if auth_scores else 0.0

        total_pos = int(np.sum(pos_counts))
        total_neg = int(np.sum(neg_counts))
        total_all = total_pos + total_neg + int(np.sum(
            [n.get("neutral_count", 0) for n in sentiment_data]
        ))

        features["positive_ratio"] = float(total_pos / total_all) if total_all else 0.0
        features["negative_ratio"] = float(total_neg / total_all) if total_all else 0.0

        return features

    def get_disclosure_count(
        self, stock_code: str, db_conn=None, date: Optional[str] = None
    ) -> Dict:
        """Count recent disclosures for a stock (최근 5일).

        2026-09-24 수정: 기존 구현은 ``news_analysis`` 에서 ``source='DART'`` 를 셌는데
        (1) stock_code 컬럼이 없어 **전 종목 동일값**이 되고 (2) ``CURRENT_DATE`` 기준이라
        과거 기준일을 못 쓴다. 실제 DART 공시 테이블(``disclosures``)이 생기면 그걸 쓰고,
        없으면 0 을 반환한다(전 종목 상수를 넣지 않는다).

        필요 계약: ``disclosures(stock_code, rcept_dt DATE, report_nm)``
        (DART_API_KEY 는 .env 에 있으므로 scripts/dart_financial_backfill.py 방식으로 수집 가능)
        """
        features = {"disclosure_count_5d": 0}

        if db_conn is None:
            return features
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'disclosures'
                )
            """)
            if not cur.fetchone()[0]:
                cur.close()
                return features
            cur.execute("""
                SELECT COUNT(*) FROM disclosures
                WHERE stock_code = %s
                  AND rcept_dt <= %s
                  AND rcept_dt >= %s::date - INTERVAL '5 days'
            """, (stock_code, date, date))
            row = cur.fetchone()
            cur.close()
            features["disclosure_count_5d"] = int(row[0]) if row else 0
        except Exception as e:
            logger.debug(f"Disclosure count failed: {e}")
            if db_conn:
                db_conn.rollback()

        return features

    def get_sentiment_from_db(
        self, stock_code: str, db_conn=None, days: int = 20, as_of: Optional[str] = None
    ) -> List[Dict]:
        """Fetch sentiment time-series from PostgreSQL.

        ``as_of`` (YYYY-MM-DD, 선택): **시점정합(as-of) 기준일**. 주면 ``analysis_date <= as_of``
        인 최근 ``days`` 행만 반환한다. 주지 않으면 종전 동작(테이블 전체에서 최신 N행)이다.

        왜(2026-10-02, CG60 실측): 종전 구현은 날짜 필터가 없어 **빌드 시점의 DB 내용**을 그대로
        썼다 → ① 과거 패널 날짜에도 미래 감성값이 들어가는 시점누수(학습구간 07-03 에 09-30·10-01
        행을 사용) ② 같은 (종목·날짜)의 값이 **언제 패널을 구웠는지에 따라 달라지는** 재현 불가
        (실측: 동일 종목집합·같은 소스인데 panel_995 news_count_5d 비영 0.00% vs
        panel_420_asofpatch_evfix 97.94% — 빌드 시점의 stock_sentiment 내용이 달랐다).
        추론 경로는 ``date=오늘`` 이라 as_of 를 줘도 결과가 동일하다(미래 행은 존재하지 않음).
        """
        if db_conn is None:
            return []

        try:
            cur = db_conn.cursor()
            # 창(window)은 **최신 days 행**을 DESC 로 뽑는다(정렬 기준을 바꾸면 창이 과거로 밀린다).
            # 그 뒤 파이썬에서 뒤집어 **오래된 → 최신(오름차순)** 으로 돌려준다 —
            # `get_aggregate_sentiment` 가 `scores[-5:]`(최근 5행)·`scores[-1] - scores[0]`(추세)로
            # 쓰기 때문이다. 종전에는 DESC 를 그대로 넘겨 `news_count_5d` 가 창의 **가장 오래된
            # 5행** 합이 되고 추세 부호가 뒤집혔다(2026-10-02 CG60: 최신 5행 합 20 이어야 할 값이 25).
            if as_of:
                cur.execute("""
                    SELECT analysis_date, avg_sentiment, sentiment_count,
                           positive_count, negative_count, neutral_count,
                           avg_authenticity
                    FROM stock_sentiment
                    WHERE stock_code = %s AND analysis_date <= %s
                    ORDER BY analysis_date DESC
                    LIMIT %s
                """, (stock_code, str(as_of)[:10], days))
            else:
                cur.execute("""
                    SELECT analysis_date, avg_sentiment, sentiment_count,
                           positive_count, negative_count, neutral_count,
                           avg_authenticity
                    FROM stock_sentiment
                    WHERE stock_code = %s
                    ORDER BY analysis_date DESC
                    LIMIT %s
                """, (stock_code, days))
            rows = list(reversed(cur.fetchall()))
            cur.close()

            return [{
                "avg_sentiment": float(r[1]) if r[1] else 0,
                "sentiment_count": int(r[2]) if r[2] else 0,
                "positive_count": int(r[3]) if r[3] else 0,
                "negative_count": int(r[4]) if r[4] else 0,
                "neutral_count": int(r[5]) if r[5] else 0,
                "avg_authenticity": float(r[6]) if r[6] else 0,
            } for r in rows]

        except Exception as e:
            logger.debug(f"Sentiment DB fetch failed for {stock_code}: {e}")
            return []

    def get_all_features(self, stock_code: str, db_conn=None, date: Optional[str] = None) -> Dict:
        """Get all sentiment-based features.

        ``date`` (선택, YYYY-MM-DD): 공시 카운트의 기준일 **이자 감성 시계열의 as-of 기준일**.
        None 이면 오늘(기존 동작 — 추론 경로와 호환).

        ⚠ ``date`` 를 주면 감성 집계(news_count_5d/20d·sentiment_avg*)도 그 날짜 기준으로만
        계산된다(2026-10-02 CG60: 종전에는 날짜를 무시해 미래 행을 읽었다).
        """
        features = {}
        as_of = str(date)[:10] if date else None
        sentiment_data = self.get_sentiment_from_db(stock_code, db_conn, as_of=as_of)
        features.update(self.get_aggregate_sentiment(sentiment_data))
        features.update(self.get_disclosure_count(stock_code, db_conn, date))
        return features
