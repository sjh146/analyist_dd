"""KIS 일봉 '미완성 당일 봉' 가드 회귀 테스트 (DB·네트워크 없음).

왜 필요한가 (실측 2026-10-01 04:19 KST)
- data_gap 백필(04:15 크론)이 **당일(10/01)을 공실로 판정**해 `kis_app.main --job daily
  --date 20261001` 을 장 개시 전에 실행했다. KIS 는 장 개시 전 당일 조회에
  '전일 종가 = 시/고/저/종가, 거래량 0' 인 미완성 스냅샷을 돌려주고, 파서가 이를 그대로
  통과시켜 market_data 에 평탄·거래량 0 봉이 종목당 1행씩 쌓였다(06:0x 실측 525종목, 진행 중).
- 파급: market_data_freshness_days = -1(신선도 왜곡 — '초신선'으로 읽힌다),
  수급 지연 프로브 판정이 02:00 의 2(통과) → 06:02 의 3(미달) 로 뒤집혔다.
- yfinance-collector 는 c5d2c08 에서 같은 가드를 가졌지만 KIS 경로에는 없었다.
가드가 약해지면 같은 오염이 조용히 재발한다(적재는 성공으로 보이고 exit code 도 0).
"""
import os
import sys
from datetime import date, datetime, timezone, timedelta

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "services", "kis-collector"))

from kis_app.utils import (  # noqa: E402
    is_unfinished_daily_bar,
    kst_market_closed,
)

KST = timezone(timedelta(hours=9))


def _kst(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def test_pre_open_today_bar_is_unfinished():
    """장 개시 전 당일 봉 = 미완성 (2026-10-01 04:19 실측 시나리오)."""
    now = _kst(2026, 10, 1, 4, 19)
    assert is_unfinished_daily_bar(date(2026, 10, 1), now=now) is True


def test_intraday_today_bar_is_unfinished():
    now = _kst(2026, 10, 1, 11, 7)  # 2026-09-23 실측(장중 스냅샷 2,562행) 재현
    assert is_unfinished_daily_bar(date(2026, 10, 1), now=now) is True


def test_after_close_today_bar_is_kept():
    """18:55 일봉 크론은 당일 확정 봉을 넣는 정상 경로 — 막으면 안 된다."""
    for hh, mm in ((15, 40), (18, 55), (23, 0)):
        now = _kst(2026, 10, 1, hh, mm)
        assert is_unfinished_daily_bar(date(2026, 10, 1), now=now) is False, (hh, mm)


def test_past_bar_is_kept_any_time():
    now = _kst(2026, 10, 1, 4, 19)
    assert is_unfinished_daily_bar(date(2026, 9, 30), now=now) is False
    assert is_unfinished_daily_bar(date(2026, 9, 29), now=now) is False


def test_future_bar_is_always_dropped():
    now = _kst(2026, 10, 1, 18, 55)
    assert is_unfinished_daily_bar(date(2026, 10, 2), now=now) is True


def test_string_and_datetime_inputs():
    now = _kst(2026, 10, 1, 4, 19)
    assert is_unfinished_daily_bar("20261001", now=now) is True
    assert is_unfinished_daily_bar(datetime(2026, 10, 1, 0, 0), now=now) is True
    assert is_unfinished_daily_bar("2026-10-01", now=now) is False  # 파싱 불가 → 호출자 판단


def test_market_closed_boundary():
    assert kst_market_closed(_kst(2026, 10, 1, 15, 39)) is False
    assert kst_market_closed(_kst(2026, 10, 1, 15, 40)) is True


def test_collector_filters_unfinished_rows():
    """DailyCollector.collect 가 미완성 당일 봉을 저장하지 않는가(가짜 클라이언트/스토리지)."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..",
        "services", "kis-collector"))
    from kis_app.collectors.daily_collector import DailyCollector

    today = datetime.now(KST).date()
    past = date(2026, 9, 30)

    def _row(d, vol):
        return {"stck_bsop_date": d.strftime("%Y%m%d"), "stck_clpr": "1000",
                "stck_oprc": "1000", "stck_hgpr": "1000", "stck_lwpr": "1000",
                "cntg_vol": str(vol)}

    class _Client:
        """요청 대상일 봉을 돌려준다 (KIS 기간별시세는 당일 조회 시 스냅샷을 준다)."""

        def __init__(self):
            self.requested = []

        def get_daily_chart(self, code, excd, start, end, count=5):
            self.requested.append(str(start))
            d = datetime.strptime(str(start), "%Y%m%d").date()
            vol = 0 if d == today else 1234  # 당일 스냅샷은 거래량 0
            return {"output2": [_row(d, vol)]}

    class _Storage:
        def __init__(self):
            self.saved = []

        def get_universe(self):
            return [("005930", "KOSPI")]

        def save_market_data(self, code, rows):
            self.saved.append((code, rows))
            return len(rows)

    def _run(target):
        st = _Storage()
        summary = DailyCollector(_Client(), st).collect(target.strftime("%Y%m%d"))
        dates = [r["trade_date"] for _c, rows in st.saved for r in rows]
        return summary, dates

    # ① 과거일은 언제든 저장된다 (가드가 정상 경로를 막지 않는다)
    summary, dates = _run(past)
    assert dates == [past]
    assert summary["unfinished"] == 0

    # ② 당일: 마감 전이면 저장 금지(= 실측 2026-10-01 04:19 사고의 차단 지점),
    #    마감 후면 정상 적재.
    summary, dates = _run(today)
    if kst_market_closed():
        assert dates == [today]
        assert summary["unfinished"] == 0
    else:
        assert dates == []
        assert summary["unfinished"] == 1
