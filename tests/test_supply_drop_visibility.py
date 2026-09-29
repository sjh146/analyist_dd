"""SupplyCollector 드롭 가시화 회귀 테스트 (DB·네트워크 없음).

WHY (2026-09-29 실측): `_drop_untraded_dates` 는 'market_data 에 그 날짜 행이 있어야 저장'
규칙이라(상장 전 패딩 제거용), 수급 크론(16:20)이 일봉 적재(18:55~)보다 먼저 돌면 **당일 수급이
전량 버려진다** — 실측: 종목별 로그 `as-of 2026-09-29` 인데 DB 의 9/29 행은 0건(최신 9/28).
그런데 종전엔 `logger.info` 만 있어 크론 로그(`tail stdout`)에 아무 흔적이 없었다 → 원인이
'상장 전 패딩'(설계)인지 '적재 순서'(결함)인지 구분 불가 = 무음 드롭.
이 테스트는 그 수치(last_dropped / dropped_total)가 실제로 남는지 고정한다.
"""
import os
import sys
from datetime import date

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "services", "kis-collector"))

from kis_app.collectors.supply_collector import SupplyCollector  # noqa: E402


class _StubCursor:
    def __init__(self, traded):
        self.traded = traded
        self.payload = None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        # psycopg2 는 date 컬럼을 datetime.date 로 돌려준다(문자열 비교 함정의 근거)
        return [(d,) for d in self.traded]

    def executemany(self, sql, payload):
        self.payload = list(payload)
        return len(self.payload)

    def close(self):
        pass


class _StubConn:
    def __init__(self, traded):
        self.cur = _StubCursor(traded)

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass


def _rows(*dates):
    return [{"trade_date": d, "foreign_net_buy": 1.0} for d in dates]


def test_dropped_rows_are_counted():
    """market_data 에 없는 날짜(당일 미적재)는 버려지되 수치로 남는다."""
    conn = _StubConn({date(2026, 9, 28)})
    c = SupplyCollector(client=None, pg_conn=conn)
    saved = c.save_flows("005930", _rows("2026-09-28", "2026-09-29"))

    assert saved == 1, "market_data 에 있는 날짜만 저장돼야 한다"
    assert c.last_dropped == 1, "버린 행수(당일 1행)가 기록돼야 한다"
    assert c.dropped_total == 1


def test_dropped_total_accumulates_per_stock():
    """여러 종목에 걸쳐 버린 행수가 합산된다(러너가 실행 단위로 보고할 수 있어야 한다)."""
    conn = _StubConn({date(2026, 9, 28)})
    c = SupplyCollector(client=None, pg_conn=conn)
    c.save_flows("005930", _rows("2026-09-28", "2026-09-29"))
    c.save_flows("000660", _rows("2026-09-28", "2026-09-29"))

    assert c.dropped_total == 2
    assert c.last_dropped == 1


def test_no_drop_when_all_dates_traded():
    """버린 행이 없으면 0 — 정상 실행에서 경고가 뜨지 않아야 한다."""
    conn = _StubConn({date(2026, 9, 28), date(2026, 9, 29)})
    c = SupplyCollector(client=None, pg_conn=conn)
    saved = c.save_flows("005930", _rows("2026-09-28", "2026-09-29"))

    assert saved == 2
    assert c.last_dropped == 0
    assert c.dropped_total == 0


def test_prelisting_padding_still_dropped():
    """설계된 동작 보존: 상장 전(all-zero) 구간은 여전히 저장하지 않는다."""
    conn = _StubConn({date(2026, 9, 4)})  # 스카이랩스 386380 실측 사례
    c = SupplyCollector(client=None, pg_conn=conn)
    saved = c.save_flows("386380", _rows("2026-05-15", "2026-09-04"))

    assert saved == 1
    assert c.last_dropped == 1
