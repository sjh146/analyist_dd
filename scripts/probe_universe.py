#!/usr/bin/env python3
"""팩터 유니버스 0 원인 계수 프로브 (읽기 전용) — L5 진단.

관측(2026-09-26): real_factor_backtest 2025-08~2026-02 구간에서 '유니버스 0 — 보유 없음(skip)',
2026-04 이후만 808~813종목. financial_statements 는 2023~2026 보유(2,592종목×4년).
→ filter_universe 의 **어느 조건**이 시점별로 몇 종목을 탈락시키는지 직접 계수한다.

컨테이너 마운트(확인됨): services/job-runner→/app, services/backtester→/opt/backtester(ro),
services/strategy-agents→/opt/strategy-agents, 루트 scripts→/opt/scripts(ro)
"""
import sys
from datetime import date

sys.path.insert(0, "/opt/strategy-agents")
sys.path.insert(0, "/opt/backtester")
sys.path.insert(0, "/app/app")

import inspect  # noqa: E402

import scripts.real_factor_backtest as R  # noqa: E402


def main():
    print("[probe] real_factor_backtest 공개 이름:", [n for n in dir(R) if not n.startswith("_")][:20])

    src = inspect.getsource(R.run_backtest)
    print("\n[probe] run_backtest 에서 유니버스/스토리지 관련 줄:")
    for i, line in enumerate(src.splitlines(), 1):
        if any(k in line for k in ("universe", "storage", "asof", "get_market_caps", "stocks")):
            print(f"  {i:4}| {line.strip()[:130]}")

    # 스토리지 팩토리가 있으면 시점별 유니버스를 직접 세어 본다.
    for name in ("make_storage", "build_storage", "_storage", "StorageAdapter", "DbStorage"):
        obj = getattr(R, name, None)
        if obj is not None:
            print(f"\n[probe] 발견: {name} = {obj}")
    print("\n[probe] STRATEGY_CLASSES:", list(getattr(R, "STRATEGY_CLASSES", {})))


if __name__ == "__main__":
    main()
