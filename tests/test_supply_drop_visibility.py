"""SupplyCollector 드롭/보존 회귀 테스트 (DB·네트워크 없음).

WHY 1 (2026-09-29 20:0x 실측): `_drop_untraded_dates` 는 'market_data 에 그 날짜 행이 있어야
저장' 규칙이라(상장 전 패딩 제거용), 수급 크론(16:20)이 일봉 적재(18:55~)보다 먼저 돌면 **당일
수급이 전량 버려진다** — 실측: 종목별 로그 `as-of 2026-09-29` 인데 DB 의 9/29 행은 0건(최신 9/28).
그런데 종전엔 `logger.info` 만 있어 크론 로그(`tail stdout`)에 아무 흔적이 없었다 → 원인이
'상장 전 패딩'(설계)인지 '적재 순서'(결함)인지 구분 불가 = 무음 드롭.
→ 1차 수리: 버린 행수를 수치로 남긴다(`last_dropped`).

WHY 2 (2026-09-29 22:0x 수리): 가시화만으로는 데이터가 살아나지 않는다. 같은 시각 실측에서
회전 유니버스 798종목 중 **792종목이 최신일(9/29) 수급 0행**(최대 지연 1거래일)이었다 —
하루 250종목 회전이라 채워지는 데 최대 3~4일이 걸리는 동안 모델의 최신 횡단면에는 수급이 없다.
→ 판정을 '시장 전체 일봉 적재일'과 비교하도록 바꿔 **사유를 분리**한다:
   · 시장은 거래했는데 이 종목 일봉이 없음(상장 전·거래정지) → 제거(`last_dropped`)
   · 시장에도 그 날짜 일봉이 아직 없음(일봉 적재 전) → **보존**(`last_kept_unloaded`)
이 테스트가 그 두 경로와 설계된 패딩 제거를 고정한다.
"""
import os
import sys
from datetime import date

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "services", "kis-collector"))

from kis_app.collectors.supply_collector import SupplyCollector  # noqa: E402


class _StubCursor:
    """SQL 두 종류(종목별 조회 / 시장 마지막 적재일)를 구분해 응답한다.

    psycopg2 는 date 컬럼을 datetime.date 로 돌려준다(문자열 비교 함정의 근거).
    """

    def __init__(self, stock_dates, market_max):
        self.stock_dates = stock_dates
        self.market_max = market_max
        self.payload = None
        self.market_queries = 0

    def execute(self, sql, params=None):
        self.sql = sql
        if "MAX(trade_date)" in sql:
            self.market_queries += 1

    def fetchall(self):
        return [(d,) for d in self.stock_dates]

    def fetchone(self):
        return (self.market_max,)

    def executemany(self, sql, payload):
        self.payload = list(payload)
        return len(self.payload)

    def close(self):
        pass


class _StubConn:
    def __init__(self, stock_dates, market_max):
        self.cur = _StubCursor(stock_dates, market_max)

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass


def _rows(*dates):
    return [{"trade_date": d, "foreign_net_buy": 1.0} for d in dates]


# 시장 일봉 마지막 적재일 (2026-09-29 16:20 시점의 실측: 최신 9/28)
_MARKET_MAX = date(2026, 9, 28)


def test_today_before_daily_load_is_kept_not_dropped():
    """핵심 회귀: 일봉 적재 전 수집된 **당일 수급을 버리지 않는다**.

    실측 2026-09-29 16:20 — 종목 로그는 as-of 9/29 인데 DB 9/29 행 0건이었다(792/798 지연).
    """
    conn = _StubConn([date(2026, 9, 28)], _MARKET_MAX)  # 9/29 는 시장에도 아직 없음
    c = SupplyCollector(client=None, pg_conn=conn)
    saved = c.save_flows("005930", _rows("2026-09-28", "2026-09-29"))

    assert saved == 2, "일봉 미적재일(당일)도 저장돼야 한다"
    assert c.kept_unloaded_total == 1 and c.last_kept_unloaded == 1
    assert c.last_dropped == 0, "적재 순서 결함은 '미거래일 제외'로 세지 않는다"


def test_prelisting_padding_still_dropped():
    """설계된 동작 보존: 상장 전(all-zero) 구간은 여전히 저장하지 않는다.

    실측 사례: 스카이랩스 386380 — 시장은 2026-05-15 에 거래했는데 그 종목 일봉은 9/4 부터.
    """
    conn = _StubConn([date(2026, 9, 4)], _MARKET_MAX)
    c = SupplyCollector(client=None, pg_conn=conn)
    saved = c.save_flows("386380", _rows("2026-05-15", "2026-09-04"))

    assert saved == 1, "상장 전 날짜는 제외된다"
    assert c.last_dropped == 1
    assert c.last_kept_unloaded == 0


def test_suspension_day_dropped():
    """거래정지일(시장은 거래, 이 종목 일봉 없음)은 제외 — 커버리지를 부풀리지 않는다."""
    conn = _StubConn([date(2026, 9, 24), date(2026, 9, 28)], _MARKET_MAX)
    c = SupplyCollector(client=None, pg_conn=conn)
    saved = c.save_flows("008290", _rows("2026-09-24", "2026-09-25", "2026-09-28"))

    assert saved == 2
    assert c.last_dropped == 1
    assert c.last_kept_unloaded == 0


def test_holiday_date_dropped_without_phantom_row():
    """휴장일(시장에 그 날 일봉이 아예 없음)도 제외한다.

    2026-09-24·25 는 추석 휴장 — market_data 에 그 날짜가 **전 종목 모두** 없다. '그 날짜가
    market_data 에 없다'만으로 판정하면 휴장일 행이 저장된다(실측 22:2x: 008290 프로브에서
    휴장일 2행이 '보존'으로 잡혔다) → 마지막 적재일 이하는 제외로 고정한다.
    """
    conn = _StubConn([date(2026, 9, 23)], _MARKET_MAX)
    c = SupplyCollector(client=None, pg_conn=conn)
    saved = c.save_flows("005930", _rows("2026-09-23", "2026-09-24", "2026-09-25"))

    assert saved == 1
    assert c.last_dropped == 2
    assert c.last_kept_unloaded == 0


def test_market_max_queried_once_per_instance():
    """시장 마지막 적재일은 **인스턴스당 1회**만 조회한다(종목마다 재조회하면 낭비)."""
    conn = _StubConn([date(2026, 9, 28)], _MARKET_MAX)
    c = SupplyCollector(client=None, pg_conn=conn)
    for code in ("005930", "000660", "005380"):
        c.save_flows(code, _rows("2026-09-28", "2026-09-29"))
    assert conn.cur.market_queries == 1, "시장 마지막 적재일 조회는 1회여야 한다"
    assert c._market_max == "2026-09-28"


def test_kept_unloaded_total_accumulates_per_stock():
    """여러 종목에 걸쳐 보존 행수가 합산된다(러너가 실행 단위로 보고할 수 있어야 한다)."""
    conn = _StubConn([date(2026, 9, 28)], _MARKET_MAX)
    c = SupplyCollector(client=None, pg_conn=conn)
    c.save_flows("005930", _rows("2026-09-28", "2026-09-29"))
    c.save_flows("000660", _rows("2026-09-28", "2026-09-29"))

    assert c.kept_unloaded_total == 2
    assert c.last_kept_unloaded == 1


def test_no_signal_when_all_dates_traded():
    """버린 행도 보존 행도 없으면 둘 다 0 — 정상 실행에서 경고가 뜨지 않아야 한다."""
    conn = _StubConn([date(2026, 9, 28), date(2026, 9, 29)], date(2026, 9, 29))
    c = SupplyCollector(client=None, pg_conn=conn)
    saved = c.save_flows("005930", _rows("2026-09-28", "2026-09-29"))

    assert saved == 2
    assert c.last_dropped == 0 and c.dropped_total == 0
    assert c.last_kept_unloaded == 0 and c.kept_unloaded_total == 0
