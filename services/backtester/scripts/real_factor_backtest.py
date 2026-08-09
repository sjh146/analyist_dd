#!/usr/bin/env python3
"""real_factor_backtest.py — 실데이터(analyist_dd postgres) 팩터 백테스트.

강환국『하면 된다! 퀀트투자』 규칙을 실 DB(market_data/financial_statements/stocks)에
적용해 리밸런싱 기반 동일가중 포트폴리오 수익률을 계산한다.

사용:
  POSTGRES_PORT=5434 python3.10 real_factor_backtest.py \
      --strategy value_factor --top-n 5 --start 2025-08-01 --end 2026-07-31

실행 환경: /usr/bin/python3.10 (psycopg2 + app import 확인됨), repo .env의
POSTGRES_USER/DB/PASSWORD 사용, POSTGRES_PORT만 호스트 매핑(5434)으로 오버라이드.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "strategy-agents"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.storage.postgres_storage import PostgresStorage
from app.strategies.value_strategy import ValueStrategy
from app.strategies.quality_strategy import QualityStrategy
from app.strategies.momentum_strategy import MomentumStrategy
from app.strategies.lowvol_strategy import LowVolatilityStrategy
from app.strategies.multifactor_strategy import MultiFactorStrategy
from app.factors.universe import filter_universe

STRATEGY_CLASSES = {
    "value_factor": ValueStrategy,
    "quality_factor": QualityStrategy,
    "momentum_factor": MomentumStrategy,
    "lowvol_factor": LowVolatilityStrategy,
    "multifactor": MultiFactorStrategy,
}
# 책 규칙: 재무 팩터 분기(63거래일), 가격 팩터 월간(21거래일) — 하드코딩, 튜닝 금지
REBALANCE_DAYS = {"value_factor": 63, "quality_factor": 63, "multifactor": 63,
                  "momentum_factor": 21, "lowvol_factor": 21}


def rebalance_dates(strategy, start: date, end: date):
    """전략의 리밸런싱 게이트(anchor + k*interval)와 정렬된 날짜 생성."""
    interval = strategy.rebalance_interval_days
    anchor = datetime.strptime(strategy.rebalance_anchor, "%Y-%m-%d").date()
    if interval <= 0:
        d = start
        while d <= end:
            yield d
            d += timedelta(days=21)
        return
    k = 0
    while True:
        d = anchor + timedelta(days=k * interval)
        if d > end:
            break
        if d >= start:
            yield d
        k += 1


def price_at(storage, code, asof, days=400):
    """asof 시점(포함)까지의 상승순 close 시리즈 → (이전가, asof가)."""
    series = storage.get_price_series_asof(code, days=days, asof_date=asof)
    if not series:
        return None, None
    prev = series[-2] if len(series) >= 2 else None
    return prev, series[-1]


def run_backtest(strategy_name, top_n, start, end, output=None):
    storage = PostgresStorage()
    stocks = storage.get_all_stocks()
    print(f"[real-backtest] stocks={len(stocks)} strategy={strategy_name} top_n={top_n} "
          f"window={start}~{end}")

    strategy = STRATEGY_CLASSES[strategy_name](storage)
    interval = REBALANCE_DAYS[strategy_name]

    prev_holdings = set()
    periods = []
    trades = 0
    for rd in rebalance_dates(strategy, start, end):
        # 리밸런싱일: 유니버스 + 시그널
        universe = filter_universe(storage, stocks, asof_date=rd)
        signals = strategy.analyze(asof_date=rd)
        buys = {s["stock_code"] for s in signals if s["action"] == "buy"}
        sells = {s["stock_code"] for s in signals if s["action"] == "sell"}
        trades += len(buys) + len(sells)
        holdings = buys if buys else prev_holdings  # 유지(재평가 불가 시 이전 유지)
        if not holdings:
            print(f"  [{rd}] 유니버스 {len(universe)} — 보유 없음 (skip)")
            prev_holdings = holdings
            continue

        # 다음 리밸런싱까지 보유 수익률 (동일가중)
        next_rd = rd + timedelta(days=interval)
        if next_rd > end:
            next_rd = end
        rets = []
        for code in holdings:
            _, p_start = price_at(storage, code, rd)
            p_end = price_at(storage, code, next_rd)[1]
            if p_start and p_end:
                rets.append(p_end / p_start - 1.0)
        period_ret = sum(rets) / len(rets) if rets else 0.0
        periods.append({"rebalance": str(rd), "holdings": sorted(holdings),
                        "n": len(holdings), "period_return": round(period_ret, 6)})
        print(f"  [{rd}] 유니버스 {len(universe)} 보유 {len(holdings)} "
              f"기간수익 {period_ret:.4%}")
        prev_holdings = holdings

    # 지표
    if periods:
        total = 1.0
        for p in periods:
            total *= (1 + p["period_return"])
        total_return = total - 1.0
        rets = [p["period_return"] for p in periods]
        avg = sum(rets) / len(rets)
        var = sum((r - avg) ** 2 for r in rets) / len(rets)
        sharpe = (avg / (var ** 0.5)) * (len(rets) ** 0.5) if var > 0 else None
        peak, max_dd = 1.0, 0.0
        for r in rets:
            peak = max(peak, peak * (1 + r))
            dd = (peak - peak * (1 + r)) / peak
            max_dd = max(max_dd, dd)
        win_rate = sum(1 for r in rets if r > 0) / len(rets)
    else:
        total_return, sharpe, max_dd, win_rate = 0.0, None, 0.0, 0.0

    result = {
        "strategy": strategy_name, "top_n": top_n, "mode": "real-db",
        "window": {"start": str(start), "end": str(end)},
        "rebalance_days": interval, "periods": len(periods),
        "metrics": {
            "total_return": round(total_return, 6),
            "sharpe": round(sharpe, 4) if sharpe else None,
            "max_drawdown": round(max_dd, 6),
            "win_rate": round(win_rate, 4),
            "num_trades": trades,
        },
        "periods_detail": periods,
    }
    out_path = output or f"../../../.omo/evidence/real-backtest-{strategy_name}.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n[real-backtest] {strategy_name}: total={total_return:.4%} "
          f"sharpe={sharpe if sharpe is None else round(sharpe,2)} "
          f"maxDD={max_dd:.4%} win={win_rate:.4%} trades={trades}")
    print(f"  -> {out_path}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, choices=sorted(STRATEGY_CLASSES))
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--start", default="2025-08-01")
    ap.add_argument("--end", default="2026-07-31")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()
    run_backtest(args.strategy, args.top_n,
                 date.fromisoformat(args.start), date.fromisoformat(args.end),
                 output=args.output)


if __name__ == "__main__":
    main()
