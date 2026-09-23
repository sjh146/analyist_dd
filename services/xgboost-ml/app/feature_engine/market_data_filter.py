"""market_data 조회 공통 필터.

KRX 일별매매정보는 **거래정지/무거래 종목에도 행을 준다** — 그 행은 시가·고가·저가가
모두 0이고 거래량도 0인 채 기준가만 종가로 들어온다(실측 2026-09-23: 28,282행 / 579종목,
최근 일평균 약 113종목).

그대로 피처에 넣으면 ATR·고저폭·변동성이 0으로, 수익률이 비정상적으로 왜곡되고,
학습 표본에도 "매매 불가능한 날"이 섞인다. 따라서 market_data를 읽는 모든 피처 조회는
아래 조건을 붙인다(원본 테이블은 손대지 않는다 — 휴장/정지 이력 자체는 남겨 둔다).

사용:
    from app.feature_engine.market_data_filter import MARKET_DATA_VALID
    cur.execute(f\"\"\"
        SELECT trade_date, close_price
        FROM market_data
        WHERE stock_code = %s AND trade_date <= %s
          AND {MARKET_DATA_VALID}
        ORDER BY trade_date
    \"\"\", (stock_code, date))
"""

MARKET_DATA_VALID = "NOT (open_price = 0 AND high_price = 0 AND low_price = 0)"
