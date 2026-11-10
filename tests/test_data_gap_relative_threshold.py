"""data_gap 상대 임계 — '부분 적재를 공실로 잡는가' 회귀 (DB·네트워크 없음).

왜 필요한가 (실측 2026-11-09)
- 18:55 KIS 일봉 수집이 553/3,979 종목에서 WSL 재부팅으로 끊겼다. 당일 적재는 1,260행
  (정상 2,986)이었다. 종전 점검은 `GAP_THRESHOLD=1000` **절대값**만 봤으므로
  1,260 >= 1,000 → '공실 없음 — 데이터 정상' 으로 통과시킬 상태였다.
- 교훈은 스킬에 이미 있다(절대 문턱은 기준선이 움직이면 신호를 잃는다). 임계를
  '최근 정상일 중위값 × 0.6' 으로 잡아 부분 적재를 공실과 같은 취급으로 만든다.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import data_gap as dg  # noqa: E402


def test_threshold_is_ratio_of_median():
    assert dg.gap_threshold([2986] * 20) == int(2986 * 0.6) == 1791


def test_threshold_falls_back_to_floor_without_baseline():
    assert dg.gap_threshold([]) == dg.GAP_THRESHOLD
    assert dg.gap_threshold([0, 0]) == dg.GAP_THRESHOLD


def test_threshold_never_below_floor_when_universe_was_small():
    # 우주가 작던 시절(예: 백필 초기 500종목/일)에 비례식이 바닥값을 뚫으면 오탐이 쏟아진다
    assert dg.gap_threshold([500] * 20) == dg.GAP_THRESHOLD


def test_threshold_median_even_and_odd():
    assert dg.gap_threshold([1000, 2000, 3000]) == int(2000 * 0.6)
    assert dg.gap_threshold([1000, 2000, 3000, 4000]) == int(2500 * 0.6)


def test_partial_day_is_flagged_as_gap(monkeypatch):
    """정상일 2,986 인데 하루가 1,260 → 상대 임계로는 공실. 절대 1,000 으로는 통과였다."""
    base = 2986
    # 날짜 고정 버그 수리: today-3 이 토·일이면 expected_dates 가 그 날을 빼서 **주말마다 실패**한다
    # (실측 2026-11-10 화: today-3 = 11-07 토 → 공실 0). 가장 최근 평일을 고른다.
    partial_d = date.today() - timedelta(days=3)
    while partial_d.weekday() >= 5:
        partial_d -= timedelta(days=1)
    partial = partial_d.isoformat()
    counts = {partial: 1260}
    # 과거 평일 전부 정상 적재 → 당일/부분일만 후보
    for i in range(1, dg.LOOKBACK_DAYS + 2):
        d = (date.today() - timedelta(days=i))
        if d.weekday() < 5:
            counts.setdefault(d.isoformat(), base)
    monkeypatch.setattr(dg, "load_holidays", lambda: set())
    monkeypatch.setattr(dg, "pg_count", lambda d: counts.get(d, 0))
    monkeypatch.setattr(dg, "recent_day_counts", lambda *a, **k: [base] * 20)
    gaps, _h = dg.find_gaps(probe=False)
    assert partial in [d for d, _n in gaps], "부분 적재일이 공실 후보에서 빠졌다"


def test_full_day_is_not_a_gap(monkeypatch):
    base = 2986
    counts = {}
    for i in range(1, dg.LOOKBACK_DAYS + 2):
        d = (date.today() - timedelta(days=i))
        if d.weekday() < 5:
            counts[d.isoformat()] = base
    monkeypatch.setattr(dg, "load_holidays", lambda: set())
    monkeypatch.setattr(dg, "pg_count", lambda d: counts.get(d, 0))
    monkeypatch.setattr(dg, "recent_day_counts", lambda *a, **k: [base] * 20)
    gaps, _h = dg.find_gaps(probe=False)
    assert gaps == [], "정상 적재일이 공실로 오탐됐다"
