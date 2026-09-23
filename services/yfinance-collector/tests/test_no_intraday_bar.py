"""당일(미완성) 봉 차단 회귀 테스트.

배경: 컨테이너 부팅 직후 전 종목 수집이 장중 스냅샷을 market_data에 써서
(1) 20:00 파이프라인이 부분 봉을 학습에 쓰고 (2) DB 최신일이 당일로 올라가
증분 수집 조건(start = DB max + 1)을 막았다. 실측 2026-09-23 11:07.

두 겹으로 막는다:
  - 요청 구간: 장 마감(15:40 KST) 전이면 end_date = D-1
  - 저장 가드: trade_date >= 오늘(KST) 행은 저장하지 않음

pytest 없이도 돌도록 __main__ 실행을 지원한다.
"""
from datetime import date, datetime, timedelta

import pandas as pd

from app.collectors.price_collector import PriceCollector
from app.config import kst_market_closed, kst_now
from app.storage.postgres_storage import _row_date


def test_pykrx_range_excludes_today_before_close():
    pc = PriceCollector()
    today = kst_now().date()
    if kst_market_closed():
        assert pc.end_date.date() == today, pc.end_date
    else:
        assert pc.end_date.date() == today - timedelta(days=1), pc.end_date


def test_row_date_normalization():
    assert _row_date(pd.Timestamp("2026-09-22")) == date(2026, 9, 22)
    assert _row_date(datetime(2026, 9, 22, 15, 30)) == date(2026, 9, 22)
    assert _row_date(date(2026, 9, 22)) == date(2026, 9, 22)
    assert _row_date("2026-09-22") == date(2026, 9, 22)
    assert _row_date(pd.NaT) is None
    assert _row_date(None) is None
    assert _row_date(float("nan")) is None


def test_save_guard_blocks_today(cleanup=True):
    """오늘(KST) 날짜 행은 저장되지 않아야 한다. DB 없으면 skip."""
    from app.storage.postgres_storage import PostgresStorage

    try:
        st = PostgresStorage()
        if not st._get_conn():
            print("skip: DB 연결 없음")
            return
        st._put_conn(st._get_conn())
    except Exception as e:  # pragma: no cover
        print(f"skip: DB 초기화 실패 {e}")
        return

    today = kst_now().date()
    df = pd.DataFrame([{
        "trade_date": today, "open": 1.0, "high": 1.0, "low": 1.0,
        "close": 123456.0, "volume": 1,
    }])
    st.save_market_data("005930", df)

    conn = st._get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM market_data WHERE stock_code='005930' AND trade_date=%s",
            (today,),
        )
        n = cur.fetchone()[0]
        cur.close()
    finally:
        st._put_conn(conn)

    if n and cleanup:
        conn = st._get_conn()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM market_data WHERE stock_code='005930' AND trade_date=%s", (today,)
        )
        conn.commit()
        cur.close()
        st._put_conn(conn)
    assert n == 0, f"당일 행이 저장됨({n}) — 저장 가드 확인 필요"


if __name__ == "__main__":
    test_pykrx_range_excludes_today_before_close()
    print("ok: 요청 구간 클램프", PriceCollector().end_date.date())
    test_row_date_normalization()
    print("ok: _row_date 정규화")
    test_save_guard_blocks_today()
    print("ok: 저장 가드 (오늘 행 미저장)")
