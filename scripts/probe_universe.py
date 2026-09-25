#!/usr/bin/env python3
"""팩터 백테스트의 유니버스 0 원인 규명 프로브 (읽기 전용).

관측(2026-09-26): real_factor_backtest 가 2025-08~2026-02 구간에서 `유니버스 0 — 보유 없음(skip)`
이었고 2026-04 이후에만 808~813 이었다. 그런데 financial_statements 는 2023~2026 을 이미 보유한다.
→ 필터의 각 조건(market_cap / 거래대금 / PIT 재무 / 상장기간)이 어느 시점에서 몇 종목을 통과시키는지
   구간별로 실측한다.
"""
import sys
from datetime import date

sys.path.insert(0, "/opt/strategy-agents")
sys.path.insert(0, "/opt/backtester")

from scripts.real_factor_backtest import STRATEGY_CLASSES, run_backtest  # noqa: F401,E402


def main():
    # storage 와 stocks 를 어떻게 얻는지 실제 함수에서 확인한다(추측 금지).
    import inspect
    import scripts.real_factor_backtest as R

    print("[probe] real_factor_backtest 모듈에서 사용 가능한 이름:")
    print("  ", [n for n in dir(R) if not n.startswith("_")][:25])

    src = inspect.getsource(R.run_backtest)
    print("\n[probe] run_backtest 소스 앞 40줄:")
    for line in src.splitlines()[:40]:
        print("   ", line)

    print("\n[probe] 유니버스 관련 호출 지점:")
    for i, line in enumerate(src.splitlines(), 1):
        if any(k in line for k in ("universe", "filter_universe", "get_market_caps", "asof")):
            print(f"    {i:4}| {line.strip()[:120]}")


if __name__ == "__main__":
    main()
