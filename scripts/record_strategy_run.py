"""전략 실행 결과 기록 헬퍼 — Grafana Quant Strategy Monitoring 대시보드용.

strategy_runs 테이블에 append-only 이력 기록. 스크립트(스크리너/백테스트/팩터/
재학습) 실행 말미에 호출한다. 실패 시 조용히 스킵 (모니터링이 실행을 깨면 안 됨).
"""
import json
import os
from datetime import datetime


def to_native(value):
    """numpy 스칼라를 네이티브 Python 타입으로 변환 (psycopg2 파라미터용).

    np.float64/np.int64 등 numpy 스칼라를 그대로 psycopg2 파라미터로 넘기면
    ``schema "np" does not exist`` 오류가 난다. 여기서 bool/int/float/str/None
    으로 정규화해 안전하게 만든다.
    """
    if value is None:
        return None
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
    except ImportError:
        pass
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float, str)):
        return value
    return value


def to_jsonable(obj):
    """meta(jsonb) 직렬화 전 numpy/비표준 값 재귀 변환 (json.dumps 가능한 타입으로).

    ndarray → list, numpy 스칼라 → 파이썬 스칼라, set/tuple → list,
    그 외 직렬화 불가 타입 → str() 폴백. 최종 안전망은 json.dumps(..., default=str).
    """
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return to_jsonable(obj.tolist())
    except ImportError:
        pass
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, dict):
        return {to_jsonable(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    try:
        json.dumps(obj)
    except (TypeError, ValueError):
        return str(obj)
    return obj


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
                    to_native(stocks),
                    to_native(errors),
                    to_native(auc),
                    to_native(accuracy),
                    to_native(metric_value),
                    json.dumps(to_jsonable(meta), ensure_ascii=False, default=str) if meta else None,
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
