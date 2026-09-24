#!/usr/bin/env python3
"""죽은 피처 되살림 검증 — 가격·유사도·베이즈·밸류에이션 백분위 계열.

실행 (stock_xgboost_ml 컨테이너, cwd=/app):
    docker exec stock_xgboost_ml python scripts/verify_feature_revival.py
    docker exec stock_xgboost_ml python scripts/verify_feature_revival.py --stocks 5 --date-offsets 0,10,20

출력:
  1) 피처 × (종목,날짜) 매트릭스 (날짜에 따라 값이 달라지는지 눈으로 확인)
  2) 각 피처의 nonzero 비율 / 표준편차 (상수 여부 판정)
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

TARGET_FEATURES = [
    "atr", "atr_pct", "bb_width", "bb_position", "relative_strength",
    "avg_similarity_top10", "max_similarity", "similarity_std", "similar_count",
    "similar_stocks_return_avg",
    "per_percentile", "pbr_percentile",
    "bayes_momentum_1d", "bayes_momentum_5d", "bayes_volatility", "bayes_gain_uncertainty",
    "momentum_3_12m", "momentum_1m_reverse",
]

MARKET_DATA_VALID = "NOT (open_price = 0 AND high_price = 0 AND low_price = 0)"


def pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=3)
    ap.add_argument("--date-offsets", default="0,10,20",
                    help="가장 최근 거래일 기준 오프셋(거래일 수)")
    args = ap.parse_args()

    for p in ("/app", os.getcwd()):
        if p not in sys.path:
            sys.path.insert(0, p)

    from app.feature_engine.feature_pipeline import FeaturePipeline
    from app.training.universe import select_training_universe

    pg = pg_connect()
    offsets = [int(x) for x in args.date_offsets.split(",") if x.strip() != ""]
    try:
        # select_training_universe 는 date_from(기본 최근 60일) 창에서 min_days 개
        # 이상 거래된 종목을 고른다 → min_days 를 60보다 크게 주면 항상 빈 결과다.
        stocks = select_training_universe(pg, limit=args.stocks, min_days=30, seed=0)
        if not stocks:
            print("학습 유니버스가 비었습니다")
            return 1

        cur = pg.cursor()
        cur.execute("SELECT DISTINCT trade_date::text FROM market_data ORDER BY trade_date")
        all_dates = [r[0] for r in cur.fetchall()]
        cur.close()
        pick_dates = [all_dates[-(o + 1)] for o in offsets if o + 1 <= len(all_dates)]

        pipeline = FeaturePipeline(pg_conn=pg)
        rows = []
        for code in stocks:
            cur = pg.cursor()
            cur.execute(f"""
                SELECT trade_date, open_price, high_price, low_price, close_price, volume
                FROM market_data
                WHERE stock_code = %s AND {MARKET_DATA_VALID}
                ORDER BY trade_date
            """, (code,))
            mrows = cur.fetchall()
            cur.close()
            if not mrows:
                continue
            mdf = pd.DataFrame(
                mrows,
                columns=["trade_date", "open", "high", "low", "close", "volume"],
            )
            for d in pick_dates:
                try:
                    feats = pipeline.build_features(code, d, market_df=mdf)
                except Exception as e:  # noqa: BLE001
                    print(f"  build 실패 {code} {d}: {e}")
                    continue
                rec = {"stock_code": code, "date": d,
                       "history_days": int((pd.to_datetime(mdf["trade_date"]) <= d).sum())}
                for f in TARGET_FEATURES:
                    rec[f] = feats.get(f)
                rows.append(rec)

        if not rows:
            print("피처 빌드 결과가 없습니다")
            return 1

        df = pd.DataFrame(rows)
        pd.set_option("display.width", 250)
        pd.set_option("display.max_columns", 50)
        pd.set_option("display.float_format", lambda v: f"{v:.6g}")

        print("\n=== 1) 피처 × (종목,날짜) 값 매트릭스 ===")
        print(df[["stock_code", "date", "history_days"] + TARGET_FEATURES].to_string(index=False))

        print("\n=== 2) 피처별 nonzero 비율 / 표준편차 (전체 표본) ===")
        summary = []
        for f in TARGET_FEATURES:
            s = pd.to_numeric(df[f], errors="coerce")
            nz = int((s.fillna(0) != 0).sum())
            summary.append({
                "feature": f,
                "nonzero": f"{nz}/{len(s)}",
                "nonzero_ratio": round(nz / len(s), 3),
                "std": round(float(s.fillna(0).std(ddof=0)), 8),
                "n_distinct": int(s.nunique(dropna=True)),
                "min": float(s.min()) if s.notna().any() else None,
                "max": float(s.max()) if s.notna().any() else None,
                "alive": "OK" if nz > 0 and float(s.fillna(0).std(ddof=0)) > 0 else "DEAD",
            })
        print(pd.DataFrame(summary).to_string(index=False))

        print("\n=== 3) 날짜 민감도(종목별, 날짜 간 값 변화) ===")
        for code in df["stock_code"].unique():
            sub = df[df["stock_code"] == code]
            changed = [f for f in TARGET_FEATURES
                       if pd.to_numeric(sub[f], errors="coerce").nunique(dropna=True) > 1]
            print(f"  {code}: 날짜에 따라 변하는 피처 {len(changed)}개 "
                  f"/ {len(TARGET_FEATURES)} — {', '.join(changed) if changed else '(없음)'}")
        return 0
    finally:
        pg.close()


if __name__ == "__main__":
    sys.exit(main())
