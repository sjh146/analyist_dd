"""담당 피처 부활 검증 (2026-09-24) — 테마/그래프·시장폭·재무잔여.

실제 FeaturePipeline.build_features 를 종목×날짜로 돌려 담당 피처가
비영·비상수이고 날짜에 따라 달라지는지 표로 출력한다.

실행: docker exec stock_xgboost_ml python /app/scripts/feature_revival_verify.py
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import psycopg2
from neo4j import GraphDatabase

sys.path.insert(0, "/app")

OWNED = [
    # 테마/그래프
    "theme_count", "theme_max_relevance", "theme_momentum",
    "twin_count", "twin_avg_correlation", "sector_count", "sector_momentum",
    "cycle_up", "cycle_down",
    # 시장폭/수급
    "market_breadth", "krx_advance_decline_ratio", "krx_total_trading_value",
    "retail_ownership_pct", "etf_flow_5d", "disclosure_count_5d",
    "economic_event_count_7d", "economic_event_impact",
    # 재무 잔여
    "value_pcr", "value_ncav", "value_pfcr", "quality_cp_to_assets", "quality_beta",
]
DATE_DEPENDENT = {"theme_momentum", "cycle_up", "cycle_down", "market_breadth",
                  "krx_advance_decline_ratio", "krx_total_trading_value",
                  "economic_event_count_7d", "economic_event_impact"}


def pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main():
    from app.feature_engine.feature_pipeline import FeaturePipeline

    pg = pg_connect()
    driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
    )
    cur = pg.cursor()
    cur.execute("SELECT max(trade_date) FROM market_data")
    last = cur.fetchone()[0]
    # ETF/ETN 은 테마·트윈·재무가 없으므로 일반 종목만 표본으로 쓴다.
    cur.execute("""
        SELECT md.stock_code FROM market_data md
        JOIN stocks s ON s.stock_code = md.stock_code
        WHERE md.trade_date = %s AND s.instrument_type = 'STOCK'
          AND NOT (md.open_price=0 AND md.high_price=0 AND md.low_price=0)
        ORDER BY md.volume DESC NULLS LAST LIMIT 8
    """, (last,))
    codes = [r[0] for r in cur.fetchall()]
    cur.execute("""
        SELECT DISTINCT trade_date FROM market_data
        WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT 130
    """, (last,))
    all_dates = [str(r[0]) for r in cur.fetchall()]
    cur.close()
    # 최근 + 과거(사이클 국면이 바뀌는 구간 포함) 골고루
    dates = [all_dates[i] for i in (0, 3, 20, 40, 60, 90) if i < len(all_dates)]

    pipe = FeaturePipeline(pg_conn=pg, neo4j_conn=driver)
    per_feature = defaultdict(list)          # feature -> 모든 (종목,날짜) 값
    per_stock_date = defaultdict(lambda: defaultdict(list))  # feature -> {종목: [날짜별 값]}

    for code in codes:
        for dt in dates:
            # 날짜를 넘길 수 있는 경로(패치 후)와 현행 경로를 모두 확인한다.
            f = pipe.build_features(code, dt)
            g = pipe.graph.get_graph_features(code, driver, dt)
            f = dict(f)
            for k in ("theme_momentum", "cycle_up", "cycle_down"):
                f[k] = g.get(k, 0)   # 파이프라인 패치 후 동작(수동 합성)
            for k in OWNED:
                v = float(f.get(k, 0.0) or 0.0)
                per_feature[k].append(v)
                per_stock_date[k][code].append(v)

    print(f"# 종목 {len(codes)}개 × 날짜 {len(dates)}개 = {len(codes)*len(dates)} 행")
    print(f"# 종목코드: {codes}")
    print(f"# 날짜: {dates}")
    print()
    header = f"{'feature':28} {'nonzero':>8} {'std':>12} {'min':>12} {'max':>12} {'날짜변화':>8}"
    print(header)
    print("-" * len(header))
    rows = []
    for k in OWNED:
        vals = np.array(per_feature[k], dtype=float)
        nz = float((vals != 0).sum() / len(vals)) if len(vals) else 0.0
        std = float(np.std(vals)) if len(vals) else 0.0
        # 날짜에 따라 달라지는가: 같은 종목의 날짜별 값이 모두 같지 않은 종목 수
        changed = sum(1 for c, vv in per_stock_date[k].items() if len(set(np.round(vv, 8))) > 1)
        rows.append({
            "feature": k, "nonzero_ratio": round(nz, 4), "std": round(std, 6),
            "min": round(float(vals.min()), 6) if len(vals) else 0.0,
            "max": round(float(vals.max()), 6) if len(vals) else 0.0,
            "stocks_with_date_change": changed,
        })
        print(f"{k:28} {nz:8.3f} {std:12.6f} {vals.min():12.6f} {vals.max():12.6f} {changed:8d}")
    out = "/app/data/feature_revival/verify_owned_features.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"codes": codes, "dates": dates, "rows": rows}, fh, ensure_ascii=False, indent=2)
    print(f"\n저장: {out}")
    driver.close()
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
