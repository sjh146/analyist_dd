"""피처 커버리지 리포트 — 죽은 피처(상수 0) 조기 발견.

WHY: 173피처 중 상당수가 항상 0(상수)인데도 이를 알아챌 신호가 없었다.
이 스크립트는 훈련 패널의 각 피처에 대해 nonzero 비율과 표준편차를 계산해
``feature_coverage`` 테이블에 upsert한다. postgres-exporter 커스텀 쿼리가 이
테이블을 읽어 ``feature_dead_count`` / ``feature_alive_count`` 메트릭으로
노출하므로, Prometheus/Grafana 에서 시계열로 추적할 수 있다.

실행 (xgboost-ml 컨테이너, cwd=/app):
    docker exec stock_xgboost_ml python scripts/feature_coverage_report.py

패널 캐시(app/models/exp_panel)가 있으면 읽기만 하고 재사용한다(다른 에이전트가
쓰고 있을 수 있으므로 쓰기 금지). 캐시가 없으면 소량(30종목)으로 축소 빌드한다.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# 패널에서 진단/식별자로 취급해 피처 집계에서 제외할 컬럼
ID_COLUMNS = {"stock_code", "date", "feature_count"}

PANEL_CACHE = os.path.join("app", "models", "exp_panel", "panel.pkl")
PANEL_META = os.path.join("app", "models", "exp_panel", "meta.json")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS feature_coverage (
    feature_name TEXT PRIMARY KEY,
    nonzero_ratio DOUBLE PRECISION NOT NULL,
    std DOUBLE PRECISION NOT NULL,
    window_days INTEGER NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

UPSERT_SQL = """
INSERT INTO feature_coverage (feature_name, nonzero_ratio, std, window_days, computed_at)
VALUES (%s, %s, %s, %s, now())
ON CONFLICT (feature_name) DO UPDATE SET
    nonzero_ratio = EXCLUDED.nonzero_ratio,
    std = EXCLUDED.std,
    window_days = EXCLUDED.window_days,
    computed_at = EXCLUDED.computed_at
"""


def _pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _ensure_app_on_path():
    for p in ("/app", os.getcwd()):
        if p and p not in sys.path:
            sys.path.insert(0, p)


def _load_panel_cached():
    """exp_panel 캐시(있으면)를 읽기 전용으로 로드. (panel_df, window_days) 반환.

    캐시가 없으면 (None, None) 반환.
    """
    if not (os.path.exists(PANEL_CACHE) and os.path.exists(PANEL_META)):
        return None, None
    try:
        window_days = 120
        with open(PANEL_META, "r", encoding="utf-8") as f:
            meta = json.load(f)
            window_days = int(meta.get("days", window_days))
        df = pd.read_pickle(PANEL_CACHE)
        return df, window_days
    except Exception as e:  # noqa: BLE001
        print(f"[feature_coverage] exp_panel 캐시 로드 실패(무시): {e}")
        return None, None


def _build_small_panel(pg, n_stocks=30, days=30):
    """캐시 부재 시 소량 축소 빌드 (대형 학습/빌드 아님)."""
    _ensure_app_on_path()
    from app.feature_engine.feature_pipeline import FeaturePipeline
    from app.training.universe import select_training_universe

    stocks = select_training_universe(pg, limit=n_stocks, min_days=30, seed=0)
    pipeline = FeaturePipeline(pg_conn=pg)
    end = datetime.now()
    start = end - timedelta(days=days)
    df = pipeline.build_training_features(
        stocks, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    if df is None or len(df) == 0:
        raise RuntimeError("축소 패널 빌드 실패 (결과 없음)")
    return df, days


def compute_coverage(df, window_days):
    """각 피처의 nonzero 비율/표준편차 계산."""
    rows = []
    for col in df.columns:
        if col in ID_COLUMNS:
            continue
        s = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        n = len(s)
        nonzero = int((s != 0).sum())
        ratio = (nonzero / n) if n else 0.0
        std = float(s.std(ddof=0)) if n else 0.0
        rows.append({
            "feature_name": col,
            "nonzero_ratio": round(float(ratio), 6),
            "std": round(float(std), 6),
            "window_days": int(window_days),
        })
    return rows


def upsert_coverage(pg, rows):
    cur = pg.cursor()
    try:
        cur.execute(CREATE_TABLE_SQL)
        for r in rows:
            cur.execute(
                UPSERT_SQL,
                (r["feature_name"], r["nonzero_ratio"], r["std"], r["window_days"]),
            )
        pg.commit()
    finally:
        cur.close()


def main():
    ap = argparse.ArgumentParser(description="피처 커버리지 리포트")
    ap.add_argument("--ignore-cache", action="store_true",
                    help="exp_panel 캐시를 무시하고 지금 새로 축소 패널을 빌드한다 "
                         "(캐시가 있으면 기본은 캐시 재사용 — 캐시가 낡았으면 결과도 낡는다)")
    ap.add_argument("--stocks", type=int, default=30, help="축소 패널 종목 수")
    ap.add_argument("--days", type=int, default=30, help="축소 패널 기간(일)")
    args = ap.parse_args()

    pg = _pg_connect()
    try:
        df, window_days = None, None
        if not args.ignore_cache:
            df, window_days = _load_panel_cached()
        source = "exp_panel 캐시"
        if df is None:
            df, window_days = _build_small_panel(pg, n_stocks=args.stocks, days=args.days)
            source = f"축소 빌드({args.stocks}종목/{args.days}일)"

        rows = compute_coverage(df, window_days)
        dead = [r for r in rows if r["nonzero_ratio"] == 0]
        alive = [r for r in rows if r["nonzero_ratio"] > 0]
        upsert_coverage(pg, rows)

        print(f"[feature_coverage] source={source} window_days={window_days} "
              f"features={len(rows)}")
        print(f"[feature_coverage] 죽은 피처(=nonzero_ratio 0) {len(dead)}개 / "
              f"살아있는 피처 {len(alive)}개")
        if dead:
            print(f"[feature_coverage] 죽은 피처 목록({len(dead)}): "
                  + ", ".join(sorted(r["feature_name"] for r in dead)))
        return 0
    finally:
        pg.close()


if __name__ == "__main__":
    sys.exit(main())
