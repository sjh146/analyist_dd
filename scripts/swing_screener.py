#!/usr/bin/env python3
"""KOSDAQ Swing Stock Screener — ML-powered stock discovery.

Loads trained EnsembleModel, builds features for all KOSDAQ stocks,
predicts up-probability, filters confidence >= 0.65, outputs Top 20
to console table + CSV.
"""

import sys
import os
import logging
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'services', 'xgboost-ml'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'services', 'xgboost-ml'))

import psycopg2
import numpy as np
import pandas as pd

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "stock_secure_password_2026")
CONFIDENCE_THRESHOLD = 0.65


def get_pg_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def get_kosdaq_stocks(pg_conn):
    """Get all KOSDAQ stocks with sufficient market data."""
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT s.stock_code, s.stock_name, COALESCE(s.sector, 'Unknown') as sector,
               MAX(md.trade_date) as latest_date
        FROM stocks s
        JOIN market_data md ON s.stock_code = md.stock_code
        WHERE s.market = 'KOSDAQ'
        GROUP BY s.stock_code, s.stock_name, s.sector
        HAVING COUNT(*) >= 20
        ORDER BY s.stock_code
    """)
    rows = cur.fetchall()
    cur.close()
    return rows  # [(code, name, sector, latest_date), ...]


def main():
    today = datetime.now().strftime("%Y-%m-%d")

    pg = get_pg_conn()
    stocks = get_kosdaq_stocks(pg)
    logger.info(f"Screening {len(stocks)} KOSDAQ stocks...")

    pipeline = FeaturePipeline(pg_conn=pg)
    ensemble = EnsembleModel(model_dir="app/models/saved_models")
    ensemble.load("app/models/saved_models")

    if not ensemble._is_trained:
        logger.error("No trained model found. Run training first.")
        pg.close()
        sys.exit(1)

    candidates = []
    feature_names = pipeline.get_feature_names()
    errors = 0

    for i, (code, name, sector, latest_date) in enumerate(stocks):
        if (i + 1) % 100 == 0:
            logger.info(f"  Progress: {i + 1}/{len(stocks)}")

        try:
            features = pipeline.build_features(code, str(latest_date))
            if not features or features.get("feature_count", 0) < 10:
                continue

            feature_vector = np.array(
                [float(features.get(f, 0.0)) for f in feature_names],
                dtype=np.float32,
            )
            feature_vector = np.nan_to_num(feature_vector, nan=0.0)

            prob = float(ensemble.predict(np.array([feature_vector]))[0])

            if prob >= CONFIDENCE_THRESHOLD:
                expected_return_pct = (prob - 0.5) * 2.0 * 100.0
                candidates.append({
                    "stock_code": code,
                    "stock_name": name,
                    "sector": sector,
                    "confidence": round(prob, 4),
                    "expected_return": round(expected_return_pct, 2),
                })

        except Exception as e:
            errors += 1
            if errors <= 3:
                logger.debug(f"Error screening {code}: {e}")
            continue

    pg.close()

    # Sort by confidence descending
    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    top20 = candidates[:20]

    # Print table
    print(f"\nTop KOSDAQ Swing Candidates ({today})")
    print(f"{'Rank':<5} {'Code':<8} {'Name':<20} {'Sector':<15} {'Confidence':<12} {'Exp.Ret':<10}")
    print("-" * 70)

    if not top20:
        print(f"\n  No candidates meeting confidence threshold ({CONFIDENCE_THRESHOLD}).")
        print(f"  Model AUC=0.555 — consider retraining with more features/data.")
    else:
        for rank, c in enumerate(top20, 1):
            print(
                f"  {rank:<4} {c['stock_code']:<8} {c['stock_name']:<20} "
                f"{c['sector']:<15} {c['confidence']:<12.4f} +{c['expected_return']:.1f}%"
            )

    # Save CSV
    if top20:
        csv_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
        os.makedirs(csv_dir, exist_ok=True)
        csv_path = os.path.join(csv_dir, f"swing_candidates_{today}.csv")
        pd.DataFrame(top20).to_csv(csv_path, index=False)
        logger.info(f"CSV saved: {csv_path}")

    print(f"\nTotal screened: {len(stocks)}, Candidates: {len(candidates)}, Errors: {errors}")


if __name__ == "__main__":
    main()
