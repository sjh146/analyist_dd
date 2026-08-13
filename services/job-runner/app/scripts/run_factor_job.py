#!/usr/bin/env python3
"""온디맨드 강환국 투자팩터 5종 백테스트 잡.

real_factor_backtest.run_backtest를 5전략(value/quality/momentum/lowvol/multifactor)에
순회 적용 — 실 DB(market_data/financial_statements) 기반 리밸런싱 백테스트.
결과: factor_strategies_result.json 기록 + stdout 마지막 줄 JSON.
"""
import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, "/opt/strategy-agents")
sys.path.insert(0, "/opt/backtester")


def main():
    from scripts.real_factor_backtest import STRATEGY_CLASSES, run_backtest

    start, end = date(2025, 8, 1), date(2026, 7, 31)
    top_n = 5
    factor_dir = os.getenv("FACTOR_REPORTS_DIR", "/app/factor_reports")
    os.makedirs(factor_dir, exist_ok=True)

    strategies = {}
    for name in sorted(STRATEGY_CLASSES):
        out_path = os.path.join(factor_dir, f"real-backtest-{name}.json")
        r = run_backtest(name, top_n, start, end, output=out_path)
        strategies[name] = {
            "metrics": r.get("metrics", {}),
            "window": r.get("window", {}),
            "periods": r.get("periods", 0),
        }

    result = {
        "request_type": "factor_report",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "strategies": strategies,
    }

    with open(os.path.join(factor_dir, "factor_strategies_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
