"""
universe - 유니버스 필터.

임계값은 강환국『하면 된다! 퀀트투자』 규칙 + 한국 시장 유동성 보정(계획서 심층분석 2·3)에
따라 하드코딩한다. ⚠️ 과최적화 금지: 아래 기본값은 튜닝하지 않는다. 명시적 한국 시장 근거가
있을 때만 파라미터로 오버로딩한다.
"""

from datetime import datetime, timedelta

from app.factors.financial_snapshot import FinancialSnapshot

MIN_MARKET_CAP = 5e10
MIN_AVG_TRADING_VALUE = 1e9
MIN_QUARTERS = 1
MIN_LISTING_DAYS = 252


def filter_universe(
    storage,
    stocks,
    asof_date=None,
    min_market_cap=MIN_MARKET_CAP,
    min_avg_trading_value=MIN_AVG_TRADING_VALUE,
    min_quarters=MIN_QUARTERS,
    min_listing_days=MIN_LISTING_DAYS,
):
    """Filter stocks to the tradable factor universe, point-in-time.

    Returns list of stock codes passing: market cap >= min_market_cap,
    avg trading value (30d) >= min_avg_trading_value, >= min_quarters of
    point-in-time financial data, and listed for >= min_listing_days.
    Stocks with missing data (NULL market cap, no prices, no financials)
    are silently excluded - never an error.

    PIT 정합(L5c): 시총·거래대금도 asof 기준으로 조회한다. 스토리지에 PIT 게터가
    없으면(테스트 스텁 등) 종전과 동일한 현재값 경로로 폴백해 동작을 바꾸지 않는다.
    """
    if not stocks:
        return []

    market_caps = storage.get_market_caps()
    snapshot = FinancialSnapshot(storage)
    # PIT 게터 유무(스텁 호환) — 없으면 현행(현재값) 경로로 폴백한다.
    tv_asof = getattr(storage, "get_avg_trading_value_asof", None)
    px_asof = getattr(storage, "get_price_series_asof", None)
    px_now = getattr(storage, "get_latest_price", None)
    has_px_pit = callable(px_asof) and callable(px_now)

    if asof_date is None:
        asof_date = datetime.now().date()
    elif not hasattr(asof_date, "strftime"):
        asof_date = datetime.strptime(str(asof_date), "%Y-%m-%d").date()
    listing_cutoff = asof_date - timedelta(days=min_listing_days)

    result = []
    for stock in stocks:
        code = stock["stock_code"]
        # 시총 하한: 현재 시총을 asof/now 종가비로 환산한 PIT 프록시(증자·감자 미반영 근사).
        # 가격을 못 구하면 현행(현재 시총)을 그대로 쓴다 — 조용한 제외를 만들지 않는다.
        market_cap = market_caps.get(code)
        if market_cap is not None and has_px_pit:
            series = px_asof(code, days=1, asof_date=asof_date)
            now_px = px_now(code)
            if series and now_px:
                market_cap = market_cap * (series[-1] / now_px)
        if market_cap is None or market_cap < min_market_cap:
            continue
        # 거래대금: asof 기준 직전 30행 AVG (PIT). 게터가 없으면 현행 경로 폴백.
        if callable(tv_asof):
            avg_trading_value = tv_asof(code, days=30, asof_date=asof_date)
        else:
            avg_trading_value = storage.get_avg_trading_value(code, days=30)
        if avg_trading_value is None or avg_trading_value < min_avg_trading_value:
            continue
        if len(snapshot.get_history(code, asof_date=asof_date)) < min_quarters:
            continue
        first_date = storage.get_first_trade_date(code)
        if first_date is not None:
            first_date = datetime.strptime(str(first_date)[:10], "%Y-%m-%d").date()
            if first_date > listing_cutoff:
                continue
        result.append(code)
    return result
