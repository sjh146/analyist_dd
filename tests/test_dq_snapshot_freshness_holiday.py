"""dq_snapshot 신선도 판정(휴장일 인식) 회귀 테스트.

WHY: 신선도를 **달력일**로 고정 문턱(3/5) 판정하면 두 방향으로 틀렸다(실측 2026-09-26).
  · 과대 — 추석 휴장(9/24~25) + 주말이면 달력 3일이 정상인데 warn, 9/28(월) 틱은 5일 = 위반 오탐.
  · 과소 — 같은 문턱은 진짜 2거래일 적재 실패를 warn 으로 숨긴다.
→ '휴장일을 제외한 거래일 지연'으로 환산하고 문턱을 warn 1 / breach 2 로 고정한다.
휴장일 근거는 data/krx_holidays.json (KIS 프로브가 no_data 반환한 날짜만 기록).
"""
import importlib.util
import pathlib
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dq_snapshot", ROOT / "scripts" / "dq_snapshot.py")
dq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dq)

# 2026 추석: 9/24(목)·9/25(금) 휴장. 9/23(수)이 마지막 거래일이었다.
CHUSEOK = {"2026-09-24", "2026-09-25"}
WARN, BREACH = 1.0, 2.0
KST = timezone(timedelta(hours=9))


def _now(y, m, d, h):
    """틱 시각(KST). 신선도 판정은 시각에 따라 달라진다(UTC 날짜·스윕 완료 여부)."""
    return datetime(y, m, d, h, tzinfo=KST)


def _st(cal_days, today, holidays=CHUSEOK, now=None):
    return dq.verdict(dq.trading_days_behind(cal_days, today, holidays, now=now), WARN, BREACH)


def test_holiday_plus_weekend_is_ok_not_warn():
    """실측 2026-09-26(토) freshness=3 → 거래일 0 = **정상**. 종전 문턱은 warn 을 띄웠다."""
    assert dq.trading_days_behind(3, date(2026, 9, 26), CHUSEOK) == 0
    assert _st(3, date(2026, 9, 26)) == "ok"


def test_monday_preopen_after_holiday_weekend_is_ok_not_warn():
    """실측 2026-09-28(월) 04:00 freshness=4 → 거래일 0 = **정상**.

    DB 실측: 마지막 거래일 9/23, 9/24·25 휴장, 9/26·27 주말 → 결번 0.
    그런데 종전 구현은 '오늘(9/28)'을 그대로 세어(장도 안 열린 04:00 에) warn 1 을 띄웠다.
    같은 오류가 정상 거래일 09:00 KST 이후 16·18·20·22 시 틱마다 반복된다 → 상시 오탐.
    """
    t = _now(2026, 9, 28, 4)
    assert dq.trading_days_behind(4, t.date(), CHUSEOK, now=t) == 0
    assert _st(4, t.date(), now=t) == "ok"


def test_normal_trading_day_afternoon_is_ok():
    """정상 거래일 16:00: 마지막 바는 전일(10/13), UTC 날짜는 오늘(10/14) → 결번 0.

    종전 구현은 전일 바를 '오늘 것'으로 오독해 매 거래일 오후 틱마다 warn 을 띄웠다.
    """
    for h in (16, 18, 20, 22):
        t = _now(2026, 10, 14, h)
        assert dq.trading_days_behind(1, t.date(), set(), now=t) == 0, h
        assert _st(1, t.date(), set(), now=t) == "ok", h


def test_real_missing_friday_bar_is_warn_on_monday():
    """진짜 결번은 잡는다: 금요일(10/9) 바가 없으면 월요일 04:00 에 지연 1 = warn.

    종전 구현(KST 날짜 기준)은 마지막 적재일을 10/9 로 오독해 0(정상)으로 **실명**했다.
    """
    t = _now(2026, 10, 12, 4)          # UTC 날짜 10/11
    assert dq.trading_days_behind(3, t.date(), set(), now=t) == 1
    assert _st(3, t.date(), set(), now=t) == "warn"


def test_two_missing_trading_days_is_breach():
    """이틀 결번(10/8·10/9)은 월요일 04:00 에 지연 2 = breach."""
    t = _now(2026, 10, 12, 4)
    assert dq.trading_days_behind(4, t.date(), set(), now=t) == 2
    assert _st(4, t.date(), set(), now=t) == "breach"


def test_monday_0000_tick_gives_collection_a_margin():
    """00:00 KST 틱은 전일 바가 아직 저장 중일 수 있다(18:00 스윕이 자정~새벽에 저장) →
    전일을 요구하지 않고 하루 여유를 둔다. 02:00 이후에는 같은 상태를 결번으로 잡는다."""
    t0, t2 = _now(2026, 10, 14, 0), _now(2026, 10, 14, 2)
    assert dq.trading_days_behind(1, t0.date(), set(), now=t0) == 0     # 유예: 아직 요구 안 함
    assert dq.trading_days_behind(1, t2.date(), set(), now=t2) == 1     # 02:00 → 결번(10/13)
    assert dq.trading_days_behind(0, t0.date(), set(), now=t0) == 0     # 종가가 들어와 있으면 정상
    assert dq.trading_days_behind(0, t2.date(), set(), now=t2) == 0


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
