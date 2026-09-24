"""
News Event Features
Extracts features from the news intelligence pipeline (news_events clusters and
news_event_extraction structured events) stored in PostgreSQL.

Phase 5 of the news intelligence pipeline. Provides:

- ``market_impact_score``: recent N-hour article count vs past-average surge,
  weighted by cluster importance and combined with novelty/importance.
- ``event_<type>_5d``: occurrence count of each major taxonomy event over the
  last 5 days (e.g. ``event_realized_5d``, ``event_mna_5d``).
- ``theme_exposure_5d``: recent theme exposure strength from the themes JSONB.

All features are >= 0.0 and default to 0.0 when data is absent.

시간 정합(time alignment) — 2026-09-24:
``date`` 인자를 받는 모든 조회는 "그 날짜까지"의 데이터만 본다(as-of).
- ``date=None`` (기본): 기존 동작 — ``now()`` 기준 최근 N일/시간 윈도우.
- ``date=<date|datetime>``: 그 날짜의 **끝(23:59:59.999999)** 을 기준시각으로 삼아
  동일한 상대 윈도우를 계산한다. 예) event_*_5d 는
  ``event_date BETWEEN <date> - INTERVAL '5 days' AND <date>``.

``date=None`` 은 하위호환용이며, 학습/백테스트처럼 과거 시점 피처를 만들 때는
반드시 ``date`` 를 넘겨야 한다. 그렇지 않으면 (a) 모든 날짜의 피처가 '오늘'
기준으로 동일해져 라벨과 무관해지고 (b) 미래 정보 누수(look-ahead)가 발생한다
(2026-09 실측: 000250 의 2026-09-16 값 == 2026-09-23 값).
"""

import logging
from datetime import date as _date, datetime, time as _time, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class NewsEventFeatures:
    """Features derived from news event clusters and structured extractions."""

    # Korean taxonomy -> English lowercase feature suffix.
    EVENT_TYPE_MAP: Dict[str, str] = {
        "실적발표": "realized",
        "M&A": "mna",
        "유상증자·감자": "capital_increase",
        "CB·BW": "cb_bw",
        "지분변동": "stake_change",
        "수주": "contract",
        "신제품": "new_product",
        "특허": "patent",
        "규제": "regulation",
        "소송": "litigation",
        "부도·상폐·거래정지": "delisting",
        "리콜": "recall",
        "자사주": "treasury",
        "임원변경": "exec_change",
        "파트너십": "partnership",
        "거시경제": "macro",
        "시장지수·유동성": "market_liquidity",
        "자연재해": "disaster",
    }

    # Recent window (hours) for the surge numerator.
    RECENT_HOURS = 24
    # Past window (days) for the baseline average.
    PAST_DAYS = 7
    # Event lookback (days) for the event_<type>_5d features.
    EVENT_LOOKBACK_DAYS = 5
    # Theme lookback (days) for theme_exposure_5d.
    THEME_LOOKBACK_DAYS = 5
    # market_impact_score 상한 (폭발/무한 방지, 2026-08: 과거 기준 없을 때 1e-8 나눗셈으로 1억배 폭주)
    MAX_IMPACT_SCORE = 100.0

    def __init__(self) -> None:
        self._event_feature_names = [
            f"event_{suffix}_5d" for suffix in self.EVENT_TYPE_MAP.values()
        ]

    # ------------------------------------------------------------------
    # as-of anchor
    # ------------------------------------------------------------------
    @staticmethod
    def _anchor(date=None) -> datetime:
        """조회 기준시각(as-of).

        ``date`` 가 None 이면 ``now()`` (하위호환 = 기존 동작).
        날짜가 주어지면 그 날짜의 끝(23:59:59.999999) — 즉 그 날짜까지 발행된
        기사/이벤트만 보도록 상대 윈도우를 뒤로 민다.
        """
        if date is None:
            return datetime.now()
        if isinstance(date, datetime):
            d = date.date()
        else:
            d = date
        return datetime.combine(d, _time(23, 59, 59, 999999))

    @staticmethod
    def _as_date(date=None):
        """윈도우 상한 날짜 (date=None 이면 오늘)."""
        return NewsEventFeatures._anchor(date).date()

    # ------------------------------------------------------------------
    # market_impact_score
    # ------------------------------------------------------------------
    def _get_recent_and_past_article_stats(
        self, stock_code: str, db_conn, date=None
    ) -> Dict:
        """Return recent (24h) and past (7d) article counts + importance sums.

        ``date`` 가 주어지면 그 날짜 끝을 기준시각으로 써서 윈도우를 계산한다
        (as-of, 미래 정보 누수 없음).

        Returns a dict with keys ``recent_count``, ``recent_importance``,
        ``past_count``, ``past_importance`` (floats, >= 0). Empty on failure.
        """
        result = {
            "recent_count": 0.0,
            "recent_importance": 0.0,
            "past_count": 0.0,
            "past_importance": 0.0,
        }
        if db_conn is None:
            return result

        now = self._anchor(date)
        recent_start = now - timedelta(hours=self.RECENT_HOURS)
        past_start = now - timedelta(days=self.PAST_DAYS)

        try:
            cur = db_conn.cursor()
            # Recent window: clusters whose last article fell within the window.
            cur.execute(
                """
                SELECT COALESCE(SUM(article_count), 0),
                       COALESCE(SUM(total_importance), 0)
                FROM news_events
                WHERE stock_code = %s
                  AND last_article_at >= %s
                  AND last_article_at <= %s
                """,
                (stock_code, recent_start, now),
            )
            row = cur.fetchone()
            if row:
                result["recent_count"] = float(row[0] or 0)
                result["recent_importance"] = float(row[1] or 0)

            # Past window: clusters whose last article fell within the past
            # window but before the recent window (baseline).
            cur.execute(
                """
                SELECT COALESCE(SUM(article_count), 0),
                       COALESCE(SUM(total_importance), 0)
                FROM news_events
                WHERE stock_code = %s
                  AND last_article_at >= %s
                  AND last_article_at < %s
                """,
                (stock_code, past_start, recent_start),
            )
            row = cur.fetchone()
            if row:
                result["past_count"] = float(row[0] or 0)
                result["past_importance"] = float(row[1] or 0)
            cur.close()
        except Exception as e:
            logger.debug("news event stats failed for %s: %s", stock_code, e)
            if db_conn:
                db_conn.rollback()
        return result

    def _get_novelty_importance(self, stock_code: str, db_conn, date=None) -> Dict:
        """Return average novelty and importance from recent extractions.

        ``date`` 가 주어지면 그 날짜 끝을 기준으로 최근 24h 윈도우를 본다.
        """
        result = {"avg_novelty": 0.0, "avg_importance": 0.0}
        if db_conn is None:
            return result

        now = self._anchor(date)
        recent_start = now - timedelta(hours=self.RECENT_HOURS)
        try:
            cur = db_conn.cursor()
            cur.execute(
                """
                SELECT COALESCE(AVG(novelty), 0), COALESCE(AVG(importance), 0)
                FROM news_event_extraction
                WHERE stock_code = %s
                  AND created_at >= %s
                  AND created_at <= %s
                """,
                (stock_code, recent_start, now),
            )
            row = cur.fetchall()
            cur.close()
            if row:
                result["avg_novelty"] = float(row[0][0] or 0)
                result["avg_importance"] = float(row[0][1] or 0)
        except Exception as e:
            logger.debug("novelty/importance failed for %s: %s", stock_code, e)
            if db_conn:
                db_conn.rollback()
        return result

    def market_impact_score(self, stock_code: str, db_conn=None, date=None) -> Dict:
        """Compute the market impact score (>= 0.0).

        surge = recent_count / (past_avg + eps), where past_avg is the past
        window's per-day article count. The surge is weighted by the recent
        cluster importance and combined with the average novelty/importance of
        recent extractions. Returns 0.0 when there is no recent activity.

        ``date`` 를 주면 그 날짜 끝(as-of)을 기준으로 최근 24h / 과거 7d
        윈도우를 계산한다 — 과거 시점 피처 재현(학습)에 필수.
        """
        if db_conn is None:
            return {"market_impact_score": 0.0}

        stats = self._get_recent_and_past_article_stats(stock_code, db_conn, date)
        recent_count = stats["recent_count"]
        past_count = stats["past_count"]

        if recent_count <= 0:
            return {"market_impact_score": 0.0}

        # Per-day baseline over the past window.
        past_avg = past_count / self.PAST_DAYS
        if past_avg > 0:
            surge = recent_count / past_avg
        else:
            # 과거 기준(베이스라인)이 없으면 1e-8 나눗셈으로 폭발하지 않도록
            # 최근 건수 자체를 서지로 취급한다 (2026-08 실측: recent/1e-8 = 1억배).
            surge = float(recent_count)

        # Cluster importance weight: recent importance relative to a floor.
        importance_weight = 1.0 + stats["recent_importance"]

        # Novelty/importance combination from recent extractions.
        ni = self._get_novelty_importance(stock_code, db_conn, date)
        novelty_importance = 1.0 + ni["avg_novelty"] + ni["avg_importance"]

        score = surge * importance_weight * novelty_importance
        # 무한/폭발 방지 상한 — 시장충격도는 유한한 범위를 가져야 한다.
        return {"market_impact_score": float(min(max(score, 0.0), self.MAX_IMPACT_SCORE))}

    # ------------------------------------------------------------------
    # event_<type>_5d
    # ------------------------------------------------------------------
    def _get_event_counts_5d(self, stock_code: str, db_conn, date=None) -> Dict:
        """Return per-taxonomy event counts over the last 5 days.

        ``date`` 가 주어지면 ``event_date BETWEEN <date> - INTERVAL '5 days'
        AND <date>`` (그 날짜까지, as-of). None 이면 오늘까지(기존 동작).
        """
        counts = {suffix: 0.0 for suffix in self.EVENT_TYPE_MAP.values()}
        if db_conn is None:
            return counts

        target_date = self._as_date(date)
        try:
            cur = db_conn.cursor()
            # 이벤트 윈도우: event_date BETWEEN <조회일> - INTERVAL 'N days' AND <조회일>
            # (N = EVENT_LOOKBACK_DAYS, 클래스 상수 정수라 리터럴로 안전하게 박는다)
            cur.execute(
                f"""
                SELECT event_type, COUNT(*)
                FROM news_events
                WHERE stock_code = %s
                  AND event_date >= %s::date - INTERVAL '{int(self.EVENT_LOOKBACK_DAYS)} days'
                  AND event_date <= %s::date
                GROUP BY event_type
                """,
                (stock_code, target_date, target_date),
            )
            rows = cur.fetchall()
            cur.close()
            for event_type, cnt in rows:
                suffix = self.EVENT_TYPE_MAP.get(event_type)
                if suffix is not None:
                    counts[suffix] = float(cnt or 0)
        except Exception as e:
            logger.debug("event counts failed for %s: %s", stock_code, e)
            if db_conn:
                db_conn.rollback()
        return counts

    def event_features_5d(self, stock_code: str, db_conn=None, date=None) -> Dict:
        """Return ``event_<type>_5d`` features (counts, >= 0.0)."""
        counts = self._get_event_counts_5d(stock_code, db_conn, date)
        return {f"event_{suffix}_5d": counts[suffix] for suffix in counts}

    # ------------------------------------------------------------------
    # theme_exposure_5d
    # ------------------------------------------------------------------
    def _get_theme_exposure(self, stock_code: str, db_conn, date=None) -> float:
        """Return theme exposure strength over the last 5 days.

        Counts the number of distinct themes appearing in recent extractions,
        weighted by their frequency. Returns 0.0 when absent.
        ``date`` 가 주어지면 그 날짜 끝(as-of)까지의 5일 윈도우를 본다.
        """
        if db_conn is None:
            return 0.0

        now = self._anchor(date)
        start = now - timedelta(days=self.THEME_LOOKBACK_DAYS)
        try:
            cur = db_conn.cursor()
            cur.execute(
                """
                SELECT themes
                FROM news_event_extraction
                WHERE stock_code = %s
                  AND created_at >= %s
                  AND created_at <= %s
                """,
                (stock_code, start, now),
            )
            rows = cur.fetchall()
            cur.close()

            theme_counts: Dict[str, int] = {}
            for (themes,) in rows:
                if not themes:
                    continue
                if isinstance(themes, dict):
                    theme_list = themes.get("themes") or themes.get("theme") or []
                elif isinstance(themes, list):
                    theme_list = themes
                else:
                    theme_list = []
                for theme in theme_list:
                    if isinstance(theme, str) and theme:
                        theme_counts[theme] = theme_counts.get(theme, 0) + 1
            return float(sum(theme_counts.values()))
        except Exception as e:
            logger.debug("theme exposure failed for %s: %s", stock_code, e)
            if db_conn:
                db_conn.rollback()
            return 0.0

    def theme_exposure(self, stock_code: str, db_conn=None, date=None) -> Dict:
        """Return ``theme_exposure_5d`` feature (>= 0.0)."""
        return {"theme_exposure_5d": self._get_theme_exposure(stock_code, db_conn, date)}

    # ------------------------------------------------------------------
    # get_all_features
    # ------------------------------------------------------------------
    def get_all_features(self, stock_code: str, db_conn=None, date=None) -> Dict:
        """Get all news-event-based features.

        ``date`` (선택): 조회 기준일(as-of). None=기존 now() 기준 동작(하위호환).
        학습/백테스트는 반드시 date 를 넘겨야 한다 — 파이프라인 호출부 패치:
        ``feature_pipeline.py`` 의
        ``self.news_events.get_all_features(stock_code, self.pg_conn)``
        → ``self.news_events.get_all_features(stock_code, self.pg_conn, date)``
        """
        features: Dict = {}
        features.update(self.market_impact_score(stock_code, db_conn, date))
        features.update(self.event_features_5d(stock_code, db_conn, date))
        features.update(self.theme_exposure(stock_code, db_conn, date))
        return features
