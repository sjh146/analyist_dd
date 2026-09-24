"""시간 정합(date 인자) 회귀 테스트 — as-of 윈도우가 조회 날짜를 따르는가.

버그(2026-09 실측): 리더가 ``now()`` 기준 윈도우만 썼기 때문에
같은 종목의 2026-09-16 조회값과 2026-09-23 조회값이 **완전히 동일**했다.
그 결과 (a) 학습 시 라벨과 무관한 상수 피처가 되고 (b) 미래 정보 누수
(look-ahead)가 생겼다. 또한 백필 이벤트가 created_at=now() 로 '오늘' 버킷에
몰려 시간이 지나면 다시 0 으로 돌아갔다.

이 테스트는 date 인자가 (1) 기준시각을 그 날짜 끝으로 옮기고
(2) SQL 파라미터(= 윈도우)를 실제로 이동시키는지, (3) date 미지정 시
기존 동작(오늘 기준)이 유지되는지를 고정한다.
"""

from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock

import pytest

from app.feature_engine.news_event_features import NewsEventFeatures


@pytest.fixture
def nef():
    return NewsEventFeatures()


@pytest.fixture
def mock_db():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    return conn


def _executed_params(cur):
    """cur.execute(sql, params) 중 params 만 모아 반환."""
    out = []
    for call in cur.execute.call_args_list:
        args = call[0]
        if len(args) > 1:
            out.append(args[1])
    return out


# =============================================================================
# anchor
# =============================================================================


class TestAsOfAnchor:
    def test_none_means_now(self):
        anchor = NewsEventFeatures._anchor()
        assert abs((datetime.now() - anchor).total_seconds()) < 5

    def test_date_means_end_of_that_day(self):
        """date 를 주면 그 날짜 끝(23:59:59.999999) 이 기준시각."""
        anchor = NewsEventFeatures._anchor(date(2026, 5, 15))
        assert anchor == datetime.combine(date(2026, 5, 15), time(23, 59, 59, 999999))

    def test_datetime_is_truncated_to_day(self):
        anchor = NewsEventFeatures._anchor(datetime(2026, 5, 15, 9, 30))
        assert anchor.date() == date(2026, 5, 15)
        assert anchor.hour == 23

    def test_as_date_helper(self):
        assert NewsEventFeatures._as_date(date(2026, 7, 15)) == date(2026, 7, 15)


# =============================================================================
# event_<type>_5d — event_date BETWEEN date - INTERVAL 'N days' AND date
# =============================================================================


class TestEventCountsWindow:
    def test_sql_uses_given_date(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = []
        nef.event_features_5d("005930", mock_db, date(2026, 5, 15))
        sql, params = cur.execute.call_args[0]
        assert "INTERVAL '5 days'" in sql
        assert params[0] == "005930"
        # 상한 = 조회 날짜, 하한 = 조회 날짜(INTERVAL 로 5일 뺌)
        assert params[1] == date(2026, 5, 15)
        assert params[2] == date(2026, 5, 15)

    def test_window_shifts_with_date(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = []
        nef.event_features_5d("005930", mock_db, date(2026, 9, 23))
        d1 = cur.execute.call_args[0][1][1]
        cur.reset_mock()
        cur.fetchall.return_value = []
        nef.event_features_5d("005930", mock_db, date(2026, 5, 15))
        d2 = cur.execute.call_args[0][1][1]
        assert d1 == date(2026, 9, 23) and d2 == date(2026, 5, 15)
        assert d1 != d2

    def test_backward_compatible_without_date(self, nef, mock_db):
        """date 미지정 = 기존 동작(오늘까지)."""
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = []
        nef.event_features_5d("005930", mock_db)
        assert cur.execute.call_args[0][1][1] == datetime.now().date()

    def test_counts_mapped_to_features(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [("실적발표", 3)]
        out = nef.event_features_5d("005930", mock_db, date(2026, 7, 15))
        assert out["event_realized_5d"] == 3.0


# =============================================================================
# market_impact_score — 최근 24h / 과거 7d 윈도우가 날짜를 따라 이동
# =============================================================================


class TestMarketImpactWindow:
    def test_windows_recentre_on_given_date(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchone.side_effect = [(10.0, 5.0), (7.0, 3.0)]
        cur.fetchall.return_value = [(0.5, 0.5)]
        d = date(2026, 5, 15)
        nef.market_impact_score("005930", mock_db, d)
        anchor = datetime.combine(d, time(23, 59, 59, 999999))
        params = _executed_params(cur)
        # 첫 두 execute = recent([anchor-24h, anchor]) / past([anchor-7d, anchor-24h])
        assert params[0][1] == anchor - timedelta(hours=nef.RECENT_HOURS)
        assert params[0][2] == anchor
        assert params[1][1] == anchor - timedelta(days=nef.PAST_DAYS)
        assert params[1][2] == anchor - timedelta(hours=nef.RECENT_HOURS)
        # novelty 윈도우도 같은 앵커
        assert params[2][1] == anchor - timedelta(hours=nef.RECENT_HOURS)

    def test_differs_from_now_window(self, nef, mock_db):
        """date 를 주면 now() 윈도우와 파라미터가 달라야 한다(상수화 방지)."""
        cur = mock_db.cursor.return_value
        cur.fetchone.side_effect = [(10.0, 5.0), (7.0, 3.0)]
        cur.fetchall.return_value = [(0.5, 0.5)]
        nef.market_impact_score("005930", mock_db, date(2026, 5, 15))
        with_date = _executed_params(cur)[0][2]
        cur.reset_mock()
        cur.fetchone.side_effect = [(10.0, 5.0), (7.0, 3.0)]
        cur.fetchall.return_value = [(0.5, 0.5)]
        nef.market_impact_score("005930", mock_db)
        without_date = _executed_params(cur)[0][2]
        assert with_date != without_date


# =============================================================================
# theme_exposure_5d
# =============================================================================


class TestThemeWindow:
    def test_window_recentred(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = []
        d = date(2026, 9, 23)
        nef.theme_exposure("005930", mock_db, d)
        params = _executed_params(cur)[0]
        anchor = datetime.combine(d, time(23, 59, 59, 999999))
        assert params[1] == anchor - timedelta(days=nef.THEME_LOOKBACK_DAYS)
        assert params[2] == anchor

    def test_counts_themes(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchall.return_value = [({"themes": ["AI", "반도체"]},), ({"themes": ["AI"]},)]
        out = nef.theme_exposure("005930", mock_db, date(2026, 9, 23))
        assert out["theme_exposure_5d"] == 3.0


# =============================================================================
# get_all_features — 시그니처/하위호환 (파이프라인 호출부 인계용)
# =============================================================================


class TestGetAllFeaturesSignature:
    def test_accepts_positional_date(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchone.side_effect = [(0.0, 0.0), (0.0, 0.0)]
        cur.fetchall.return_value = []
        out = nef.get_all_features("005930", mock_db, date(2026, 5, 15))
        assert "market_impact_score" in out
        assert "theme_exposure_5d" in out
        assert len([k for k in out if k.startswith("event_")]) == len(nef.EVENT_TYPE_MAP)

    def test_without_date_still_supported(self, nef, mock_db):
        cur = mock_db.cursor.return_value
        cur.fetchone.side_effect = [(0.0, 0.0), (0.0, 0.0)]
        cur.fetchall.return_value = []
        out = nef.get_all_features("005930", mock_db)
        assert out["market_impact_score"] == 0.0

    def test_no_db_conn_with_date(self, nef):
        out = nef.get_all_features("005930", None, date(2026, 5, 15))
        assert out["market_impact_score"] == 0.0
        assert all(v == 0.0 for k, v in out.items() if k.startswith("event_"))
