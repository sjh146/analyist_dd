"""전략 실행 결과 기록 헬퍼 (job-runner 복사본 — scripts/record_strategy_run.py 동일본).

Grafana Quant Strategy Monitoring 대시보드용 strategy_runs 테이블에 기록.
"""
import json
import os
from datetime import datetime


def _connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("PGHOST", "postgres"),
        port=int(os.environ.get("PGPORT", "5432")),
        dbname=os.environ.get("PGDATABASE", "stock_trading"),
        user=os.environ.get("PGUSER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def record_run(
    tool: str,
    status: str = "ok",
    stocks: int = None,
    errors: int = None,
    auc: float = None,
    accuracy: float = None,
    metric_value: float = None,
    meta: dict = None,
) -> bool:
    """Insert one strategy_runs row. Returns True on success."""
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO strategy_runs
                    (tool, run_at, status, stocks, errors, auc, accuracy, metric_value, meta)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tool,
                    datetime.now(),
                    status,
                    stocks,
                    errors,
                    auc,
                    accuracy,
                    metric_value,
                    json.dumps(meta, ensure_ascii=False) if meta else None,
                ),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
        return True
    except Exception as e:
        print(f"[record_run] {tool} 기록 실패(무시): {e}")
        return False
