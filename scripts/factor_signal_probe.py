#!/usr/bin/env python3
"""factor_signal_probe.py — 팩터 5전략의 '실시간 신호' 개수를 직접 측정한다(읽기 전용).

왜(2026-11-25, FAC-01): 백로그 FAC-01 은 "run_factor_job 결과가 5전략 전부 signals=0 —
유니버스 공백"이라는 가설이었는데, 그 근거 파일(services/strategy-agents/reports/
factor_strategies_result.json, signals/top 포맷)은 **현행 run_factor_job 이 쓰지 않는 포맷**이다
(현행 코드는 metrics/window/periods 만 기록 — services/job-runner/app/scripts/run_factor_job.py L44-60).
즉 'signals=0' 은 아무도 재현하지 못하는 유령 지표였다. 실제 질문은 "전략 analyze() 가 오늘
신호를 내는가 + 유니버스가 비었는가"이므로, 그것만 직접 잰다.

측정(전부 읽기 전용): 전략별 ① filter_universe 크기 ② analyze() 매수/매도 신호 수
③ 매수 종목 상위 5. 판정은 호출자가 한다(이 스크립트는 JSON 만 남긴다).

사용: docker exec stock_job_runner python /opt/scripts/factor_signal_probe.py --asof 2026-11-25
"""
import argparse
import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, "/opt/strategy-agents")
sys.path.insert(0, "/opt/backtester")

OUT_DEFAULT = "/app/reports/factor_signal_probe.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None, help="YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from app.storage.postgres_storage import PostgresStorage
    from app.factors.universe import filter_universe
    from scripts.real_factor_backtest import STRATEGY_CLASSES

    asof = (datetime.strptime(args.asof, "%Y-%m-%d").date() if args.asof else date.today())
    out_path = args.out or OUT_DEFAULT

    storage = PostgresStorage()
    stocks = storage.get_all_stocks()
    uni = filter_universe(storage, stocks, asof_date=asof)

    strategies = {}
    for name in sorted(STRATEGY_CLASSES):
        try:
            strat = STRATEGY_CLASSES[name](storage)
            gate = bool(strat._is_rebalance_day(asof))
            sigs = strat.analyze(asof_date=asof) or []
            buys = [s for s in sigs if s.get("action") == "buy"]
            sells = [s for s in sigs if s.get("action") == "sell"]
            strategies[name] = {
                "is_rebalance_day": gate,
                "rebalance_interval_days": getattr(strat, "rebalance_interval_days", None),
                "n_signals": len(sigs),
                "n_buys": len(buys),
                "n_sells": len(sells),
                "top_buys": [s.get("stock_code") for s in buys[:5]],
                "error": None,
            }
        except Exception as e:
            strategies[name] = {"error": f"{type(e).__name__}: {e}"}

    from scripts.real_factor_backtest import REBALANCE_DAYS

    interval_audit = {}
    for name, v in strategies.items():
        if v.get("error"):
            continue
        live, bt = v.get("rebalance_interval_days"), REBALANCE_DAYS.get(name)
        interval_audit[name] = {"strategy": live, "backtest": bt, "match": live == bt}

    result = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "asof": str(asof),
        "n_stocks": len(stocks),
        "universe_size": len(uni),
        "strategies": strategies,
        "interval_audit": interval_audit,
        "n_interval_mismatch": sum(1 for a in interval_audit.values() if not a["match"]),
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
