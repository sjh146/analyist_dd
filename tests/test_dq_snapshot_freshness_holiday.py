"""dq_snapshot 신선도 판정(휴장일 인식) 회귀 테스트.

WHY: 신선도를 **달력일**로 고정 문턱(3/5) 판정하면 두 방향으로 틀렸다(실측 2026-09-26).
  · 과대 — 추석 휴장(9/24~25) + 주말이면 달력 3일이 정상인데 warn, 9/28(월) 틱은 5일 = 위반 오탐.
  · 과소 — 같은 문턱은 진짜 2거래일 적재 실패를 warn 으로 숨긴다.
→ '휴장일을 제외한 거래일 지연'으로 환산하고 문턱을 warn 1 / breach 2 로 고정한다.
휴장일 근거는 data/krx_holidays.json (KIS 프로브가 no_data 반환한 날짜만 기록).
"""
import importlib.util
import pathlib
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dq_snapshot", ROOT / "scripts" / "dq_snapshot.py")
dq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dq)

# 2026 추석: 9/24(목)·9/25(금) 휴장. 9/23(수)이 마지막 거래일이었다.
CHUSEOK = {"2026-09-24", "2026-09-25"}
WARN, BREACH = 1.0, 2.0


def _st(cal_days, today, holidays=CHUSEOK):
    return dq.verdict(dq.trading_days_behind(cal_days, today, holidays), WARN, BREACH)


def test_holiday_plus_weekend_is_ok_not_warn():
    """실측 2026-09-26(토) freshness=3 → 거래일 0 = **정상**. 종전 문턱은 warn 을 띄웠다."""
    assert dq.trading_days_behind(3, date(2026, 9, 26), CHUSEOK) == 0
    assert _st(3, date(2026, 9, 26)) == "ok"


def test_monday_morning_gap_is_warn_not_breach():
    """9/28(월) 00:00 틱(달력 5일)은 **거래일 하루** 지연 = warn. 종전 문턱은 위반 오탐이었다."""
    assert dq.trading_days_behind(5, date(2026, 9, 28), CHUSEOK) == 1
    assert _st(5, date(2026, 9, 28)) == "warn"


def test_real_two_trading_day_failure_is_breach_even_inside_holidays():
    """휴장일 사이 실제 적재 실패(9/22·23 두 거래일 결번)는 **위반**으로 잡힌다 — 실명하지 않는다."""
    assert dq.trading_days_behind(3, date(2026, 9, 24), CHUSEOK) == 2
    assert _st(3, date(2026, 9, 24)) == "breach"


def test_pure_weekend_is_ok():
    """휴장 캘린더가 비어 있어도 주말은 거래일이 아니다(토→일 0)."""
    assert dq.trading_days_behind(1, date(2026, 9, 20), set()) == 0


def test_holiday_file_unreadable_reports_not_usable():
    """파일이 없으면 (빈 집합, False) — 조용히 넘기지 않고 폴백 사실을 호출부가 알 수 있게 한다."""
    hol, ok = dq.load_holidays("/nonexistent/krx_holidays.json")
    assert hol == set() and ok is False


def test_holiday_file_parses_recorded_dates():
    """실제 저장소 파일이 읽히고 2026-09-24·25 가 들어 있다(휴장 판정의 유일한 근거)."""
    hol, ok = dq.load_holidays()
    assert ok is True
    assert {"2026-09-24", "2026-09-25"} <= hol


def test_degraded_mode_keeps_calendar_thresholds():
    """휴장 캘린더가 없을 때 달력 3일은 warn 유지 — 거래일 문턱(1)을 그대로 쓰면 주말마다 오탐."""
    assert dq.verdict(3.0, 3.0, 5.0) == "warn"
    assert dq.verdict(5.0, 3.0, 5.0) == "breach"


def test_trading_days_behind_none_safe():
    """값이 없으면 None(판정 불가) — 0 으로 뭉뚱그려 '정상'으로 만들지 않는다."""
    assert dq.trading_days_behind(None, date(2026, 9, 26), CHUSEOK) is None
