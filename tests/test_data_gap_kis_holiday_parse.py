"""data_gap `kis_is_trading_day` **파싱** 회귀 — 실제 KIS 응답으로 고정 (T33, 2026-10-09).

왜 필요한가 (실측 2026-10-09)
- 기존 `tests/test_data_gap_holiday_guard.py` 는 `kis_is_trading_day` 를 **monkeypatch 로 통째 치환**해
  판정 로직 자체를 한 번도 실행하지 않았다 → 필드 오독이 테스트를 그대로 통과했다(픽스처가 실응답과
  달랐던 함정). 실호출로 드러난 사실: KIS CTCA0903R 의 ``tr_day_yn`` 은 **휴장·주말에도 'Y'** 다.
- 종전 코드 ``opnd_yn=='Y' or tr_day_yn=='Y'`` → 어떤 날짜든 True → `find_gaps` 자가치유가
  **휴장 당일 아침에 진짜 휴장(2026-10-09 한글날)을 캘린더에서 삭제**했다
  (실측 로그: `휴장 캘린더 정정: 2026-10-09 제거 (KIS 국내휴장일조회 = 거래일)`).
- 수리 후 계약: 판정자는 ``bzdy_yn``(영업일) / ``opnd_yn``(개장일) 뿐. ``tr_day_yn`` 은 **쓰지 않는다**.
  판별 필드가 없으면 None(판별불가) — '거래일' 로 단정하지 않는다.
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import data_gap as dg  # noqa: E402

# ── 실측 KIS 응답(2026-10-09 21:1x KST 직접 호출, CTCA0903R) — 필드 그대로 ──
#   10-09 는 한글날(KRX 휴장)인데 tr_day_yn 만 'Y' 로 남는다.
KIS_1008_TRADING = {"bass_dt": "20261008", "wday_dvsn_cd": "05", "bzdy_yn": "Y", "tr_day_yn": "Y", "opnd_yn": "Y", "sttl_day_yn": "Y"}
KIS_1009_HOLIDAY = {"bass_dt": "20261009", "wday_dvsn_cd": "06", "bzdy_yn": "N", "tr_day_yn": "Y", "opnd_yn": "N", "sttl_day_yn": "N"}
KIS_1010_SAT = {"bass_dt": "20261010", "wday_dvsn_cd": "07", "bzdy_yn": "N", "tr_day_yn": "Y", "opnd_yn": "N", "sttl_day_yn": "N"}
KIS_1011_SUN = {"bass_dt": "20261011", "wday_dvsn_cd": "01", "bzdy_yn": "N", "tr_day_yn": "Y", "opnd_yn": "N", "sttl_day_yn": "N"}
KIS_1012_TRADING = {"bass_dt": "20261012", "wday_dvsn_cd": "02", "bzdy_yn": "Y", "tr_day_yn": "Y", "opnd_yn": "Y", "sttl_day_yn": "Y"}


def test_korean_thanksgiving_and_holiday_are_not_trading_days():
    """T33 재현 — tr_day_yn='Y' 만 보고 거래일로 읽으면 안 된다."""
    assert dg._parse_kis_trading_day([KIS_1009_HOLIDAY], "2026-10-09") is False, \
        "한글날(휴장)이 거래일로 판정됐다 → 자가치유가 캘린더에서 삭제한다(T33)"
    assert dg._parse_kis_trading_day([KIS_1010_SAT], "2026-10-10") is False
    assert dg._parse_kis_trading_day([KIS_1011_SUN], "2026-10-11") is False


def test_real_trading_days_are_true():
    assert dg._parse_kis_trading_day([KIS_1008_TRADING], "2026-10-08") is True
    assert dg._parse_kis_trading_day([KIS_1012_TRADING], "2026-10-12") is True


def test_row_is_selected_by_bass_dt_not_first():
    """응답은 기준일부터의 목록 — 다른 날짜 행이 앞에 와도 기준일 행을 골라야 한다."""
    rows = [KIS_1009_HOLIDAY, KIS_1010_SAT]
    assert dg._parse_kis_trading_day(rows, "2026-10-09") is False
    assert dg._parse_kis_trading_day(rows, "2026-10-10") is False


def test_no_discriminating_field_is_unknown_not_trading():
    """판별 필드가 없으면 None — '거래일' 로 단정하면 휴장이 캘린더에서 지워진다."""
    only_tr = {"bass_dt": "20261009", "tr_day_yn": "Y"}
    assert dg._parse_kis_trading_day([only_tr], "2026-10-09") is None
    assert dg._parse_kis_trading_day([], "2026-10-09") is None


def test_selfheal_keeps_real_holiday(monkeypatch):
    """find_gaps 자가치유가 실측 응답으로 진짜 휴장을 지우지 않는지(엔드투엔드 단위)."""
    today = date.today().isoformat()
    monkeypatch.setattr(dg, "load_holidays", lambda: {today})
    monkeypatch.setattr(dg, "pg_count", lambda d: 0)
    monkeypatch.setattr(dg, "probe_kis", lambda yyyymmdd: (False, True))
    monkeypatch.setattr(dg, "holiday_confirmable", lambda d: True)
    # 실측 파서를 그대로 쓴다(monkeypatch 로 판정을 통째 치환하지 않는다).
    rows = [{"bass_dt": today.replace("-", ""), "bzdy_yn": "N", "tr_day_yn": "Y", "opnd_yn": "N"}]
    monkeypatch.setattr(dg, "kis_is_trading_day", lambda d: dg._parse_kis_trading_day(rows, d))
    saved = {}
    monkeypatch.setattr(dg, "save_holidays", lambda days: saved.update(days=set(days)))
    dg.find_gaps(probe=True)
    assert today in saved.get("days", {today}), "진짜 휴장이 자가치유로 삭제됐다(T33 재발)"


def test_selfheal_still_removes_true_false_holiday(monkeypatch):
    """반대 방향 무회귀 — KIS 가 거래일이라고 하면 오탐은 걷어낸다."""
    today = date.today().isoformat()
    monkeypatch.setattr(dg, "load_holidays", lambda: {today})
    monkeypatch.setattr(dg, "pg_count", lambda d: 4000)
    rows = [{"bass_dt": today.replace("-", ""), "bzdy_yn": "Y", "tr_day_yn": "Y", "opnd_yn": "Y"}]
    monkeypatch.setattr(dg, "kis_is_trading_day", lambda d: dg._parse_kis_trading_day(rows, d))
    saved = {}
    monkeypatch.setattr(dg, "save_holidays", lambda days: saved.update(days=set(days)))
    dg.find_gaps(probe=True)
    assert today not in saved.get("days", set()), "거래일 오탐이 캘린더에 남았다"


def test_holiday_file_format_has_no_churn():
    """save_holidays 포맷은 HEAD 판본(2칸 들여쓰기)과 같아야 한다 — 재작성마다 전면 diff 방지."""
    import inspect
    src = inspect.getsource(dg.save_holidays)
    assert "indent=2" in src, "save_holidays 가 다른 들여쓰기로 쓰면 매 저장이 전면 diff 가 된다"
