"""data_gap 휴장 기록 가드 — '당일 = 휴장' 오탐 회귀 (DB·네트워크 없음).

왜 필요한가 (실측 2026-10-01 07:50 KST, 트레이더 환류 R24)
- 장 개시 전에는 **어떤 거래일도** 당일 일봉이 없다 → KIS 프로브가 ``no_data`` 를 돌려주고,
  종전 코드는 그 값을 그대로 ``data/krx_holidays.json`` 에 휴장으로 기록했다.
  실측: ``휴장 기록: 2026-10-01 (KIS no_data)`` → 캘린더 파일에 거래일이 들어가
  수집·감시·실험 창(구동기 market_hours·신선도 환산)이 통째로 꺼질 뻔했다.
  반증: KIS CTCA0903R ``opnd_yn=Y`` · 005930 장중 거래량 증가.
- 수리 후 계약: **당일은 ① 장 마감(15:40) 이후이고 ② KIS 국내휴장일조회가 '휴장'일 때만** 기록,
  판별 불가(None)면 기록하지 않는다(오탐이 실제 손해). 과거일 경로는 종전과 동일.
"""
import os
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import data_gap as dg  # noqa: E402


def _today_gap_env(monkeypatch, kis_trading):
    """오늘만 '적재 0 + KIS no_data' 인 환경 — 사고 당시의 모습."""
    monkeypatch.setattr(dg, "load_holidays", lambda: set())
    monkeypatch.setattr(dg, "pg_count", lambda d: 0)
    monkeypatch.setattr(dg, "probe_kis", lambda yyyymmdd: (False, True))
    monkeypatch.setattr(dg, "kis_is_trading_day", lambda d: kis_trading)
    saved = {}
    monkeypatch.setattr(dg, "save_holidays", lambda days: saved.update(days=set(days)))
    return saved


def test_trading_day_is_never_recorded_as_holiday(monkeypatch):
    """KIS 가 거래일이라고 하는 날은 no_data 여도 캘린더에 들어가면 안 된다 — 사고의 재현 지점."""
    saved = _today_gap_env(monkeypatch, kis_trading=True)
    dg.find_gaps(probe=True)
    assert date.today().isoformat() not in saved.get("days", set()), \
        "거래일이 휴장으로 기록됐다 → 수집·감시 창이 꺼진다"


def test_holiday_recorded_only_after_close(monkeypatch):
    """마감(15:40) 전에는 KIS 확정을 못 받으므로 보류해야 한다."""
    saved = _today_gap_env(monkeypatch, kis_trading=False)
    monkeypatch.setattr(dg, "holiday_confirmable", lambda d: False)  # 장 개시 전 시각
    dg.find_gaps(probe=True)
    assert date.today().isoformat() not in saved.get("days", set())


def test_holiday_recorded_after_close_when_kis_confirms(monkeypatch):
    """마감 후 + KIS 휴장 확인 → 기록한다(가드가 진짜 휴장을 막으면 안 된다)."""
    saved = _today_gap_env(monkeypatch, kis_trading=False)
    monkeypatch.setattr(dg, "holiday_confirmable", lambda d: True)
    dg.find_gaps(probe=True)
    assert date.today().isoformat() in saved.get("days", set())


def test_unknown_kis_verdict_does_not_record(monkeypatch):
    """KIS 판별 불가(None)는 '휴장'이 아니다 — fail-open 이 아니라 fail-safe."""
    saved = _today_gap_env(monkeypatch, kis_trading=None)
    monkeypatch.setattr(dg, "holiday_confirmable", lambda d: True)
    dg.find_gaps(probe=True)
    assert date.today().isoformat() not in saved.get("days", set())


def test_confirmable_past_date_always_true(monkeypatch):
    """과거일 휴장 확정은 시각과 무관 — 백필·캘린더 보정이 막히면 안 된다."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert dg.holiday_confirmable(yesterday) is True


def test_confirmable_today_depends_on_close_time(monkeypatch):
    """당일은 임계 시각 상수로 결정된다(파일 상수 토글 = 결정적 검정)."""
    today = date.today().isoformat()
    keep = dg.HOLIDAY_CONFIRM_HHMM
    try:
        dg.HOLIDAY_CONFIRM_HHMM = (23, 59)
        assert dg.holiday_confirmable(today) is False
        dg.HOLIDAY_CONFIRM_HHMM = (0, 0)
        assert dg.holiday_confirmable(today) is True
        assert datetime.combine(date.today(), time(0, 0)) <= datetime.now()
    finally:
        dg.HOLIDAY_CONFIRM_HHMM = keep


def test_wrongly_recorded_trading_day_is_self_healed(monkeypatch):
    """캘린더에 이미 굳은 오탐은 다음 실행에서 걷어낸다(자가치유)."""
    today = date.today().isoformat()
    monkeypatch.setattr(dg, "load_holidays", lambda: {today})
    monkeypatch.setattr(dg, "pg_count", lambda d: 4000)
    monkeypatch.setattr(dg, "kis_is_trading_day", lambda d: True)
    saved = {}
    monkeypatch.setattr(dg, "save_holidays", lambda days: saved.update(days=set(days)))
    dg.find_gaps(probe=True)
    assert today not in saved.get("days", {today}), "오탐이 캘린더에 남았다"


def test_closed_market_dates_are_still_recorded(monkeypatch):
    """과거 휴장일은 KIS 호출 없이도 기록된다(과거 경로 무회귀)."""
    past = None
    for i in range(1, dg.LOOKBACK_DAYS + 1):
        d = date.today() - timedelta(days=i)
        if d.weekday() < 5:
            past = d
            break
    monkeypatch.setattr(dg, "load_holidays", lambda: set())
    monkeypatch.setattr(dg, "pg_count", lambda d: 0)
    monkeypatch.setattr(dg, "probe_kis", lambda yyyymmdd: (False, True))
    calls = []

    def fake_kis(d):
        calls.append(d)
        return True  # 오늘은 거래일 → 기록 금지(과거일 경로만 검정)

    monkeypatch.setattr(dg, "kis_is_trading_day", fake_kis)
    saved = {}
    monkeypatch.setattr(dg, "save_holidays", lambda days: saved.update(days=set(days)))
    dg.find_gaps(probe=True)
    assert past.isoformat() in saved.get("days", set())
    assert set(calls) <= {date.today().isoformat()}, "KIS 교차확인은 당일에만 — 과거일 경로 무회귀"
