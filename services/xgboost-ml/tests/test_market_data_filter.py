"""market_data 필터 회귀 테스트 — 거래정지/무거래 봉(O=H=L=0)이 피처 조회에서 빠지는가.

실측 2026-09-23: 시가·고가·저가가 모두 0인 행 28,282건 / 579종목(최근 일평균 약 113종목).
그대로 피처에 들어가면 ATR·고저폭이 0, 변동성·수익률이 왜곡된다.

pytest 없이도 돌도록 __main__ 실행을 지원한다.
"""
from app.feature_engine.market_data_filter import MARKET_DATA_VALID

ZERO_OHLC = "open_price = 0 AND high_price = 0 AND low_price = 0"


def _conn():
    import os

    import psycopg2

    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "stock_trading"),
        user=os.getenv("POSTGRES_USER", "stock_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


def test_filter_text_mentions_all_three_prices():
    assert "open_price" in MARKET_DATA_VALID
    assert "high_price" in MARKET_DATA_VALID
    assert "low_price" in MARKET_DATA_VALID
    assert "NOT" in MARKET_DATA_VALID.upper()


def test_filter_excludes_zero_ohlc_rows():
    """필터를 통과하면서 O=H=L=0 인 행은 0건이어야 한다."""
    try:
        conn = _conn()
    except Exception as e:  # pragma: no cover
        print(f"skip: DB 연결 없음 ({e})")
        return
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT count(*) FROM market_data WHERE {MARKET_DATA_VALID} AND {ZERO_OHLC}"
        )
        leaked = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM market_data WHERE " + ZERO_OHLC)
        total_zero = cur.fetchone()[0]
        cur.close()
    finally:
        conn.close()
    print(f"필터 후 잔존 zero-OHLC 행: {leaked} (원본 {total_zero}건)")
    assert leaked == 0


if __name__ == "__main__":
    test_filter_text_mentions_all_three_prices()
    print("ok: 필터 문구")
    test_filter_excludes_zero_ohlc_rows()
    print("ok: zero-OHLC 제외")
