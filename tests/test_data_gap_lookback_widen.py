"""data_gap 점검 창 자동 확장 — 21거래일 공백을 10일 창이 못 보던 결함의 회귀 (DB·네트워크 없음).

왜 필요한가 (실측 2026-11-09)
- 호스트가 10-11~11-09(거래일 20일) 꺼져 있었고, 04:15 백필 점검은 ``LOOKBACK_DAYS=10`` 만 본다.
  마지막 적재가 그보다 오래되면 ``stale_warning()`` 이 '판단 불가'로 끝나 **아무것도 채우지 않는다**
  (실측: 11-09 '공실 없음/판단 불가' → 리뷰보드가 손으로 일자별 재실행해야 메웠다).
- 즉 창이 DB 상태와 무관하게 상수라, 장기 정지 때마다 같은 일이 반복된다.
수리: ``effective_lookback()`` 이 마지막 적재일까지 창을 넓히고(상한 MAX_AUTO_LOOKBACK_DAYS),
report/backfill 이 그 창으로 공실을 찾는다. 상한을 넘는 공백만 구간 백필로 넘긴다.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import data_gap as dg  # noqa: E402


def _db(rows, latest):
    """db_state() 만 치환 — DB 없이 결정적으로 돈다."""
    dg.db_state = lambda: (rows, latest)


def test_fresh_db_keeps_default_window():
    """정상 상태에서 창이 넓어지면 매일 수십 콜을 쓴다 — 기본값이 유지돼야 한다."""
    fresh = date.today() - timedelta(days=1)
    _db(3_200_000, fresh)
    assert dg.effective_lookback() == dg.LOOKBACK_DAYS


def test_long_outage_widens_window_to_cover_gap():
    """21거래일 공백(실측 10-11~11-09)이면 창이 그 공백 시작까지 넓어져야 한다."""
    latest = date.today() - timedelta(days=29)  # 2026-11-09 사고의 달력 29일
    _db(3_000_000, latest)
    lb = dg.effective_lookback()
    assert lb > dg.LOOKBACK_DAYS, "장기 공백인데 창이 그대로면 점검이 공백을 볼 수 없다"
    assert lb >= 30, "공백 시작일(마지막 적재 다음날)이 창 안에 들어와야 한다: %s" % lb
    assert latest.isoformat() in dg.expected_dates(lb), "마지막 적재일 자체가 점검 창에 없다"


def test_window_is_capped_and_falls_back_to_range_backfill():
    """상한(90일)을 넘는 공백은 1일 백필로 부적합 — 창은 상한에서 멈추고 stale 로 넘긴다."""
    _db(3_000_000, date.today() - timedelta(days=200))
    lb = dg.effective_lookback()
    assert lb == dg.MAX_AUTO_LOOKBACK_DAYS
    stale, msg = dg.stale_warning(lb)
    assert stale is True, "200일 공백은 stale(구간 백필 필요)로 판정돼야 한다: %s" % msg


def test_long_outage_is_no_longer_reported_as_judgement_impossible():
    """사고 재현: 21거래일 공백이 '판단 불가'가 아니라 '공실 N일'로 드러나야 한다."""
    latest = date.today() - timedelta(days=29)
    _db(3_000_000, latest)
    lb = dg.effective_lookback()
    stale, msg = dg.stale_warning(lb)
    assert stale is False, "넓힌 창 안이면 stale 이 아니어야 한다(이제 공실로 셀 수 있다): %s" % msg
    # 적재 0 인 날은 전부 공실 후보 — 21거래일 공백이면 최소 15일은 잡혀야 한다.
    dg.load_holidays = lambda: set()
    dg.pg_count = lambda d: 0
    dg.recent_day_counts = lambda *a, **k: []
    gaps, _h = dg.find_gaps(probe=False, lookback=lb)
    dates = [d for d, _n in gaps]
    assert date.today().isoformat() not in dates, "당일은 여전히 공실이 아니다"
    assert len(gaps) >= 15, "공백이 창 밖으로 새면 안 된다: 잡힌 공실 %d일" % len(gaps)
    assert latest.isoformat() in [d.isoformat() for d in
                                  (date.today() - timedelta(days=i) for i in range(0, lb + 1))]
