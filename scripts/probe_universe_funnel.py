#!/usr/bin/env python3
"""유니버스 필터 탈락 깔때기 계수 (L5 진단, 읽기 전용).

filter_universe 조건을 하나씩 개별로 적용해 **시점별로 몇 종목이 어디서 떨어지는지** 센다.
추측 금지 — storage 의 실제 메서드를 그대로 호출한다.
"""
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, "/opt/strategy-agents")
sys.path.insert(0, "/opt/backtester")
sys.path.insert(0, "/app/app")

import scripts.real_factor_backtest as R  # noqa: E402
from app.factors.financial_snapshot import FinancialSnapshot  # noqa: E402
from app.factors.universe import (MIN_AVG_TRADING_VALUE, MIN_LISTING_DAYS,  # noqa: E402
                                  MIN_MARKET_CAP, MIN_QUARTERS)

ASOF = [date(2025, 8, 17), date(2025, 12, 21), date(2026, 4, 26), date(2026, 6, 28)]


def main():
    storage = R.PostgresStorage()
    stocks = storage.get_all_stocks()
    caps = storage.get_market_caps()
    snap = FinancialSnapshot(storage)
    print(f"[funnel] 전체 종목 {len(stocks)} / market_caps 보유 {len(caps)}")
    cap_sample = list(caps.items())[:3]
    print(f"[funnel] market_caps 샘플: {cap_sample}")

    for rd in ASOF:
        n_cap = n_tv = n_fin = n_list = 0
        cutoff = rd - timedelta(days=MIN_LISTING_DAYS)
        for s in stocks:
            code = s["stock_code"]
            mc = caps.get(code)
            if mc is None or mc < MIN_MARKET_CAP:
                continue
            n_cap += 1
            tv = storage.get_avg_trading_value(code, days=30)
            if tv is None or tv < MIN_AVG_TRADING_VALUE:
                continue
            n_tv += 1
            try:
                hist = snap.get_history(code, asof_date=rd)
            except Exception as exc:      # noqa: BLE001 - 계수 목적, 예외도 신호
                hist = []
                if n_fin == 0 and n_tv == 1:
                    print(f"    ⚠ get_history 예외({code}): {type(exc).__name__}: {exc}")
            if len(hist) < MIN_QUARTERS:
                continue
            n_fin += 1
            fd = storage.get_first_trade_date(code)
            # ⚠ get_first_trade_date 는 **문자열**을 반환한다(str vs date 비교는 TypeError).
            # 유니버스 필터(universe.py)도 같은 함수를 쓰므로 타입 처리를 확인해야 한다 — 진단 포인트.
            if fd is not None:
                fd_d = fd if isinstance(fd, date) else datetime.strptime(str(fd)[:10], "%Y-%m-%d").date()
                if fd_d > cutoff:
                    continue
            n_list += 1
        print(f"  [{rd}] 전체 {len(stocks)} → 시총 {n_cap} → 거래대금 {n_tv} → PIT재무 {n_fin} → 상장 {n_list}")

    print("\n[funnel] PIT 재무 샘플 확인 (asof=2025-08-17, 통과/탈락 각 2개):")
    for s in stocks[:60]:
        code = s["stock_code"]
        h = snap.get_history(code, asof_date=ASOF[0])
        if h:
            print(f"  {code}: history {len(h)}건, 최신={h[-1] if h else None}")
            break


if __name__ == "__main__":
    main()
