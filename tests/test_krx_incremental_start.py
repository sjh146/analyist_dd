"""krx_daily 증분 시작일 계산 — '덜 찬 날'부터 다시 받는지 고정한다.

WHY (실측 2026-10-02): 시작일을 `MAX(trade_date)+1` 로 잡으면, 다른 작성자가 **오늘 봉 1건만**
넣는 순간 max 가 오늘이 되어 구간이 [오늘+1, 어제] = 공집합이 되고 로그에는 "수집 구간 없음"으로
남는다 → 그 어제 봉은 영구 결손이다(실측: 000020 1행 때문에 2026-10-02 전 종목 봉이 사라질 상황).

창은 최근 `KRX_LOOKBACK_DAYS`(기본 7)일이고, 휴장 파일·주말은 대상이 아니다.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import krx_daily  # noqa: E402

# today=2026-10-03(토) 기준 창(09-26~10-02)의 평일과 정상 커버리지
COMPLETE = {"2026-09-28": 3845, "2026-09-29": 3846, "2026-09-30": 3846,
            "2026-10-01": 3843, "2026-10-02": 3843}


class FakeCur:
    def __init__(self, counts, mx):
        self._counts, self._mx, self._rows = counts, mx, None

    def execute(self, sql, params=None):
        if "COUNT(*)" in sql:
            self._rows = [(k, v) for k, v in self._counts.items()]
        else:
            self._rows = [(self._mx,)]

    def fetchall(self):
        return self._rows or []

    def fetchone(self):
        return (self._rows or [(None,)])[0]

    def close(self):
        pass


class FakeConn:
    def __init__(self, counts, mx):
        self._c, self._m = counts, mx

    def cursor(self):
        return FakeCur(self._c, self._m)


def run(counts, mx, today, holidays=()):
    orig = krx_daily.load_json
    krx_daily.load_json = lambda *_a, **_k: list(holidays)
    try:
        return krx_daily._incremental_start(FakeConn(counts, mx), today)
    finally:
        krx_daily.load_json = orig


def test_recollects_day_with_partial_coverage():
    """오늘 봉 1건(max=today) 때문에 어제 봉이 건너뛰어지면 안 된다."""
    counts = dict(COMPLETE, **{"2026-10-02": 1})
    got = run(counts, dt.date(2026, 10, 2), dt.date(2026, 10, 3))
    assert got == dt.date(2026, 10, 2), got


def test_all_complete_falls_back_to_max_plus_one():
    got = run(COMPLETE, dt.date(2026, 10, 2), dt.date(2026, 10, 3))
    assert got == dt.date(2026, 10, 3), got


def test_heals_earliest_incomplete_weekday_in_window():
    """창 안의 공실 평일(부분 적재/미수집)도 다시 대상이 된다 — 가장 이른 날부터."""
    counts = {k: v for k, v in COMPLETE.items() if k != "2026-09-29"}
    got = run(counts, dt.date(2026, 10, 2), dt.date(2026, 10, 3))
    assert got == dt.date(2026, 9, 29), got


def test_holiday_and_weekend_are_not_targets():
    """휴장 파일에 있는 날·주말은 0행이어도 대상이 아니다(비교는 창의 평일에만)."""
    counts = {"2026-09-28": 3845, "2026-09-30": 3846, "2026-10-01": 3843, "2026-10-02": 3843}
    got = run(counts, dt.date(2026, 10, 2), dt.date(2026, 10, 3), holidays=["2026-09-29"])
    assert got == dt.date(2026, 10, 3), got
