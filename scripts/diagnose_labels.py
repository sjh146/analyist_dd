#!/usr/bin/env python3
"""Diagnose label distribution after build_features() date filter fix."""

import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'services', 'xgboost-ml'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'services', 'xgboost-ml'))

import psycopg2
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.feature_engine.market_features import MarketFeatures
from app.feature_engine.company_features import CompanyFeatures
from app.feature_engine.sentiment_features import SentimentFeatures
from app.feature_engine.macro_features import MacroFeatures
from app.feature_engine.graph_features import GraphFeatures
from app.feature_engine.vector_features import VectorFeatures

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PG_HOST = os.environ.get("POSTGRES_HOST", "localhost")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "stock_secure_password_2026")


def get_pg_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def get_sample_stocks(pg_conn, n=20):
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT md.stock_code FROM market_data md
        JOIN stocks s ON md.stock_code = s.stock_code
        WHERE s.market = 'KOSDAQ' AND md.trade_date >= '2026-01-01'
        GROUP BY md.stock_code
        HAVING COUNT(*) >= 50
        ORDER BY RANDOM() LIMIT %s
    """, (n,))
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    return codes


def diagnose_label_distribution(pg_conn, stock_codes):
    """Build features for sample stocks and check label distribution."""
    pipeline = FeaturePipeline(pg_conn=pg_conn)

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    logger.info(f"Building training features for {len(stock_codes)} stocks, {start_date} to {end_date}")

    df = pipeline.build_training_features(stock_codes, start_date, end_date)

    if df.empty:
        logger.error("No training data returned!")
        return

    logger.info(f"Total rows: {len(df)}")
    logger.info(f"Columns: {list(df.columns)}")

    if "price" in df.columns:
        prices = df["price"]
        logger.info(f"Price stats:")
        logger.info(f"  count: {prices.count()}")
        logger.info(f"  mean: {prices.mean():.2f}")
        logger.info(f"  std: {prices.std():.2f}")
        logger.info(f"  min: {prices.min():.2f}")
        logger.info(f"  max: {prices.max():.2f}")
        logger.info(f"  zero count: {(prices == 0).sum()}")
        logger.info(f"  non-zero count: {(prices != 0).sum()}")

    labels = np.zeros(len(df), dtype=int)
    if "stock_code" in df.columns and "price" in df.columns:
        for code in df["stock_code"].unique():
            mask = df["stock_code"] == code
            idx = df[mask].index
            p = df.loc[idx, "price"].values
            if len(p) >= 2:
                next_up = p[1:] > p[:-1]
                label_vals = np.zeros(len(p), dtype=int)
                label_vals[:-1] = next_up.astype(int)
                labels[idx] = label_vals

    n_labels = labels.shape[0]
    n_up = (labels == 1).sum()
    n_down = (labels == 0).sum()
    logger.info(f"Label distribution: {n_up} up ({100*n_up/max(n_labels,1):.1f}%), {n_down} down ({100*n_down/max(n_labels,1):.1f}%)")

    if n_up == 0 or n_down == 0:
        logger.error("FAIL: All labels are same class - model will learn nothing!")
    elif 0.3 < n_up / n_labels < 0.7:
        logger.info("PASS: Reasonable class balance")
    else:
        logger.warning(f"WARNING: Imbalanced labels - {100*n_up/n_labels:.1f}% up")


def diagnose_market_data(pg_conn, stock_codes):
    """Check what market_data looks like for sample stocks."""
    cur = pg_conn.cursor()
    for code in stock_codes[:5]:
        cur.execute("""
            SELECT trade_date, close_price, volume FROM market_data
            WHERE stock_code = %s ORDER BY trade_date DESC LIMIT 5
        """, (code,))
        rows = cur.fetchall()
        if rows:
            logger.info(f"  {code}: latest={rows[0][0]}, close={rows[0][1]}, vol={rows[0][2]}, rows={len(rows)}")
        else:
            logger.info(f"  {code}: NO DATA")
    cur.close()


if __name__ == "__main__":
    pg = get_pg_conn()
    stocks = get_sample_stocks(pg, n=15)
    logger.info(f"Sample stocks: {stocks}")

    logger.info("=== Market Data Check ===")
    diagnose_market_data(pg, stocks)

    logger.info("=== Label Distribution Check ===")
    diagnose_label_distribution(pg, stocks)

    pg.close()
