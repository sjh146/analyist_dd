#!/usr/bin/env python3
"""온디맨드 모델 백테스트 잡 — champion 모델 + 실 DB 데이터.

실행: BacktestRunner(model_dir=champion).run_backtest(...)
유니버스: 시가총액 상위 N 종목 (stocks.market_cap), 기본 30종목
기간: 최근 6개월 (온디맨드 범위, 비용 제어) — env로 오버라이드 가능
결과: stdout 마지막 줄 JSON + reports/backtest_result.json 기록
"""
import json
import os
import sys
from datetime import datetime, date

sys.path.insert(0, "/opt/xgboost-ml")
sys.path.insert(0, "/opt/backtester")

import psycopg2

PG = dict(
    host=os.getenv("POSTGRES_HOST", "postgres"),
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    dbname=os.getenv("POSTGRES_DB", "stock_trading"),
    user=os.getenv("POSTGRES_USER", "stock_user"),
    password=os.getenv("POSTGRES_PASSWORD", ""),
)


def top_universe(n: int = 30):
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT stock_code FROM stocks ORDER BY market_cap DESC NULLS LAST LIMIT %s",
            (n,),
        )
        rows = [r[0] for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()
    return rows or ["005930", "000660", "373220", "207940", "005380"]


def main():
    from runner import BacktestRunner

    # 온디맨드 범위: 시가총액 상위 10종목 × 최근 3개월 (전체 30종목×6개월은 20분+ 소요)
    codes = top_universe(int(os.getenv("BACKTEST_TOP_N", "10")))
    start = os.getenv("BACKTEST_START", "2026-05-01")
    end = os.getenv("BACKTEST_END", "2026-07-31")

    runner = BacktestRunner(model_dir="/opt/xgboost-ml/app/models/champion")
    res = runner.run_backtest("ml-ensemble", codes, start, end)

    auc = "n/a"
    try:
        with open("/opt/xgboost-ml/app/models/champion/auc.txt") as f:
            auc = f.read().strip()
    except OSError:
        pass

    result = {
        "request_type": "backtest",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "strategy": res.strategy,
        "model": {"name": "ml-ensemble champion", "auc": auc,
                  "buy_threshold": runner.buy_threshold},
        "window": {"start": res.start_date.isoformat(), "end": res.end_date.isoformat()},
        "universe_size": len(codes),
        "metrics": {
            "total_return": round(float(res.total_return), 6),
            "sharpe_ratio": round(float(res.sharpe_ratio), 4),
            "max_drawdown": round(float(res.max_drawdown), 6),
            "win_rate": round(float(res.win_rate), 4),
            "num_trades": int(res.num_trades),
        },
        "top_trades": [
            {
                "date": t.date.isoformat(),
                "stock_code": t.stock_code,
                "confidence": round(float(t.confidence), 4),
                "actual_return": round(float(t.actual_return), 6),
            }
            for t in res.trades[-20:]
        ],
    }

    reports_dir = os.getenv("REPORTS_DIR", "/app/reports")
    os.makedirs(reports_dir, exist_ok=True)
    with open(os.path.join(reports_dir, "backtest_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
