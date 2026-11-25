"""trading_value 결측 회귀 테스트.

배경(R45, 2026-11-20 실측): 이 수집기의 INSERT 가 trading_value 컬럼을 빼고 넣어,
이 경로가 실제로 만드는 ETF 행이 전부 NULL 로 남았다. 그 결과 시장 거래대금 합계가
1,157행(31%) · 17.99조 vs close*volume 28.21조 = 36% 과소집계됐고, 유동성 필터와
시장패널이 반토막 값을 읽었다. 원천에 값이 있으면 그 값을, 없으면 close*volume 근사를
넣되, 결측은 0 이 아니라 NULL 로 남긴다(계약 #2).
"""
import logging
from datetime import date

import pandas as pd

from app.storage.postgres_storage import PostgresStorage, approx_trading_value

log = logging.getLogger(__name__)


def test_approx_prefers_source_value():
    assert approx_trading_value(
        {"close": 1000.0, "volume": 10, "trading_value": 12345.0}) == 12345.0


def test_approx_falls_back_to_close_times_volume():
    assert approx_trading_value({"close": 1000.0, "volume": 10}) == 10000.0


def test_approx_keeps_null_when_value_unknown():
    assert approx_trading_value({"close": 1000.0, "volume": 0}) is None
    assert approx_trading_value({"close": None, "volume": 10}) is None
    assert approx_trading_value({"종가": None, "거래량": None}) is None


def test_save_fills_trading_value_in_db():
    """과거 날짜 1행을 실제로 적재해 trading_value 가 채워지는지 본다(검증행은 삭제)."""
    try:
        st = PostgresStorage()
        conn = st._get_conn()
        if not conn:
            log.warning("skip: DB 연결 없음")
            return
        st._put_conn(conn)
    except Exception as e:
        log.warning("skip: DB 초기화 실패 %s", e)
        return

    code, d = "005930", date(2015, 1, 2)
    df = pd.DataFrame([{
        "trade_date": d, "open": 100.0, "high": 100.0, "low": 100.0,
        "close": 1000.0, "volume": 10,
    }])
    st.save_market_data(code, df)

    conn = st._get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT trading_value FROM market_data WHERE stock_code=%s AND trade_date=%s",
            (code, d))
        row = cur.fetchone()
        cur.execute("DELETE FROM market_data WHERE stock_code=%s AND trade_date=%s", (code, d))
        conn.commit()
        cur.close()
    finally:
        st._put_conn(conn)

    assert row is not None, "행이 적재되지 않음"
    assert row[0] is not None, "trading_value 가 NULL"
    assert float(row[0]) == 10000.0, row[0]
