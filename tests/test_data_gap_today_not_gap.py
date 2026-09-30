"""data_gap.find_gaps — '당일은 공실이 아니다' 회귀 테스트 (DB·네트워크 없음).

왜 필요한가 (실측 2026-10-01 04:19 KST)
- 백필 경로는 ``find_gaps(probe=False)`` 를 쓴다. 종전에는 '당일 제외'가 probe 분기
  **안에만** 있어서(``if not exists or d == today_s``) probe=False 경로에서는 당일이
  그대로 공실로 남았다. 04:15 크론이 매 영업일 당일을 공실로 판정 →
  ``kis_app.main --job daily --date <오늘>`` 을 **장 개시 전에** 실행 →
  KIS 가 돌려준 미완성 당일 봉(평탄 OHLC·거래량 0)이 종목당 1행씩 적재됐다
  (06:0x 실측 525종목·진행 중, 원장 아님 — DB 실측).
- 파급: ``market_data_freshness_days = -1``(신선도가 '초신선'으로 읽힘),
  수급 지연 프로브 판정 2(통과) → 3(미달) 오탐.
당일 봉은 마감 후 공식 경로(18:55 daily_bars / 20:00 파이프라인)가 넣는다.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import data_gap as dg  # noqa: E402


def _patch(monkeypatch, counts):
    monkeypatch.setattr(dg, "load_holidays", lambda: set())
    monkeypatch.setattr(dg, "pg_count", lambda d: counts.get(d, 0))


def test_today_is_never_a_gap_without_probe(monkeypatch):
    """probe=False(백필 경로)에서 당일이 공실로 남으면 안 된다 — 사고의 재현 지점."""
    _patch(monkeypatch, {})  # 모든 날 적재 0 (장 개시 전 당일과 같은 모습)
    gaps, _h = dg.find_gaps(probe=False)
    today = date.today().isoformat()
    dates = [d for d, _n in gaps]
    assert today not in dates, "당일이 공실 후보에 남았다 → 장 개시 전 KIS 백필이 다시 돈다"
    assert gaps, "과거 평일 공실은 계속 잡혀야 한다(가드가 정상 탐지를 막으면 안 된다)"


def test_past_gap_still_detected(monkeypatch):
    """과거 평일의 진짜 공실은 그대로 검출 — 가드가 과잉 차단이 아니다."""
    today = date.today()
    past = None
    for i in range(1, dg.LOOKBACK_DAYS + 1):
        d = today - timedelta(days=i)
        if d.weekday() < 5:
            past = d
            break
    assert past is not None
    _patch(monkeypatch, {past.isoformat(): 5})  # 그 날만 적재 5종목(임계 미만)
    gaps, _h = dg.find_gaps(probe=False)
    assert (past.isoformat(), 5) in gaps
    assert date.today().isoformat() not in [d for d, _n in gaps]


def test_today_excluded_in_probe_path_too(monkeypatch):
    """probe=True(보고 경로)에서도 당일은 공실로 세지 않는다 (종전 동작 유지)."""
    _patch(monkeypatch, {})
    monkeypatch.setattr(dg, "probe_kis", lambda yyyymmdd: (True, False))
    gaps, _h = dg.find_gaps(probe=True)
    assert date.today().isoformat() not in [d for d, _n in gaps]
