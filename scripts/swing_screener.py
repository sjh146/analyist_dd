#!/usr/bin/env python3
"""KOSDAQ Swing Stock Screener — ML-powered stock discovery.

Loads trained EnsembleModel, builds features for all KOSDAQ stocks,
predicts up-probability, filters confidence >= 0.65, outputs Top 20
to console table + CSV.
"""

import sys
import os
import argparse
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


def parse_args():
    parser = argparse.ArgumentParser(description='Swing trade candidate screener')
    parser.add_argument('--include-krx-data', action='store_true',
                        help='Include KRX market data (foreign net buy, program trading, short selling) in scoring')
    parser.add_argument('--include-economic-events', action='store_true',
                        help='Include economic calendar impact in scoring')
    parser.add_argument('--output', type=str, default=None,
                        help='Output CSV path (default: data/ directory with date-based filename)')
    return parser.parse_args()


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


def get_krx_foreign_net_buy(pg_conn, stock_code, lookback_days=5):
    """Get recent foreign net buy for a stock (positive = foreign buying)."""
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT SUM(foreign_net_buy) FROM foreign_institutional
            WHERE stock_code = %s AND trade_date >= CURRENT_DATE - %s
        """, (stock_code, lookback_days))
        result = cur.fetchone()
        cur.close()
        return float(result[0]) if result and result[0] else 0.0
    except Exception:
        return 0.0


def get_krx_program_trading(pg_conn, stock_code, lookback_days=5):
    """Get recent program trading net value for a stock."""
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT SUM(program_net) FROM program_trading
            WHERE stock_code = %s AND trade_date >= CURRENT_DATE - %s
        """, (stock_code, lookback_days))
        result = cur.fetchone()
        cur.close()
        return float(result[0]) if result and result[0] else 0.0
    except Exception:
        return 0.0


def get_krx_short_selling(pg_conn, stock_code, lookback_days=5):
    """Get recent short selling ratio (lower = more bullish)."""
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT AVG(short_selling_ratio) FROM short_selling
            WHERE stock_code = %s AND trade_date >= CURRENT_DATE - %s
        """, (stock_code, lookback_days))
        result = cur.fetchone()
        cur.close()
        return float(result[0]) if result and result[0] else 0.0
    except Exception:
        return 0.0


def get_economic_impact(pg_conn, lookahead_days=7):
    """Get max importance of upcoming economic events within lookahead_days."""
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT MAX(importance) FROM economic_calendar
            WHERE event_date BETWEEN CURRENT_DATE AND CURRENT_DATE + %s
              AND importance IS NOT NULL
        """, (lookahead_days,))
        result = cur.fetchone()
        cur.close()
        return int(result[0]) if result and result[0] else 0
    except Exception:
        return 0


def apply_krx_score_boost(candidates, pg_conn):
    """Apply score boost based on KRX data signals."""
    for c in candidates:
        code = c["stock_code"]
        foreign_net = get_krx_foreign_net_buy(pg_conn, code)
        program_net = get_krx_program_trading(pg_conn, code)
        short_ratio = get_krx_short_selling(pg_conn, code)

        boost = 1.0
        if foreign_net > 0:
            boost += 0.03
        if program_net > 0:
            boost += 0.02
        if short_ratio > 0 and short_ratio < 3.0:
            boost += 0.01

        c["confidence"] = min(round(c["confidence"] * boost, 4), 1.0)
        c["expected_return"] = round(
            (c["confidence"] - 0.5) * 2.0 * 100.0, 2
        )
    return candidates


def apply_economic_impact(candidates, pg_conn):
    """Adjust scores based on upcoming economic event importance."""
    impact = get_economic_impact(pg_conn)
    if impact >= 3:
        factor = 0.95
    elif impact == 2:
        factor = 0.98
    else:
        factor = 1.0

    if factor < 1.0:
        for c in candidates:
            c["confidence"] = min(round(c["confidence"] * factor, 4), 1.0)
            c["expected_return"] = round(
                (c["confidence"] - 0.5) * 2.0 * 100.0, 2
            )
    return candidates


def main():
    args = parse_args()
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

    if args.include_krx_data:
        logger.info("Applying KRX data score boost...")
        candidates = apply_krx_score_boost(candidates, pg)

    if args.include_economic_events:
        logger.info("Applying economic calendar impact...")
        candidates = apply_economic_impact(candidates, pg)

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
        if args.output:
            csv_path = args.output
            csv_dir = os.path.dirname(csv_path)
            if csv_dir:
                os.makedirs(csv_dir, exist_ok=True)
        else:
            csv_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
            os.makedirs(csv_dir, exist_ok=True)
            csv_path = os.path.join(csv_dir, f"swing_candidates_{today}.csv")
        pd.DataFrame(top20).to_csv(csv_path, index=False)
        logger.info(f"CSV saved: {csv_path}")

    print(f"\nTotal screened: {len(stocks)}, Candidates: {len(candidates)}, Errors: {errors}")


if __name__ == "__main__":
    main()
