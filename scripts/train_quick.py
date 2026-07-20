#!/usr/bin/env python3
"""Quick validation: train on 10 KOSDAQ stocks x 90 days to verify AUC > 0.5."""

import sys
import os
import logging
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'services', 'xgboost-ml'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'services', 'xgboost-ml'))

import psycopg2
import numpy as np
from datetime import datetime, timedelta

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.training.trainer import Trainer

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "stock_secure_password_2026")


def get_pg_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def get_training_stocks(pg_conn, n=10):
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT md.stock_code FROM market_data md
        JOIN stocks s ON md.stock_code = s.stock_code
        WHERE s.market = 'KOSDAQ' AND md.trade_date >= '2026-04-01'
        GROUP BY md.stock_code
        HAVING COUNT(*) >= 50
        ORDER BY md.stock_code LIMIT %s
    """, (n,))
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    return codes


def main():
    pg = get_pg_conn()
    stock_codes = get_training_stocks(pg, n=30)
    logger.info(f"Validation on {len(stock_codes)} KOSDAQ stocks: {stock_codes}")

    pipeline = FeaturePipeline(pg_conn=pg)
    ensemble = EnsembleModel(model_dir="app/models/saved_models")
    trainer = Trainer(storage=None, feature_pipeline=pipeline)

    logger.info("Preparing training data (180 days)...")
    result = trainer.prepare_training_data(stock_codes=stock_codes, days=180)
    X_train, X_val, X_test, y_train, y_val, y_test = result

    if X_train is None:
        logger.error("Training data preparation failed!")
        return

    n_features = X_train.shape[1]
    logger.info(f"Data ready: {len(X_train)} train, {len(X_val)} val, {len(X_test)} test, {n_features} features")

    feature_names_path = "app/models/saved_models"
    feature_names = pipeline.get_feature_names()
    df = pipeline.build_training_features(
        stock_codes,
        (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d"),
        datetime.now().strftime("%Y-%m-%d"),
    )
    if df is not None and not df.empty:
        available_features = [c for c in feature_names if c in df.columns]
        X_check = df[available_features].values.astype(np.float32)
        X_check = np.nan_to_num(X_check, nan=0.0)
        col_stds = np.std(X_check, axis=0)
        varying_mask = col_stds > 0
        varying_features = [f for f, m in zip(available_features, varying_mask) if m]
        ensemble.save_feature_names(varying_features, feature_names_path)
        logger.info(f"Saved {len(varying_features)} feature names to {feature_names_path}")

    logger.info("Training ensemble...")
    metrics = ensemble.train(X_train, y_train, X_val, y_val)
    logger.info(f"Training metrics: {json.dumps(metrics, indent=2, default=str)}")

    ensemble.save(feature_names_path)

    test_probs = ensemble.predict(X_test)
    test_preds = (test_probs > 0.5).astype(int)

    from sklearn.metrics import accuracy_score, roc_auc_score, f1_score, classification_report
    accuracy = accuracy_score(y_test, test_preds)
    f1 = f1_score(y_test, test_preds, zero_division=0)
    try:
        auc = roc_auc_score(y_test, test_probs)
    except ValueError:
        auc = 0.5

    logger.info(f"Test: accuracy={accuracy:.4f}, f1={f1:.4f}, auc={auc:.4f}")
    logger.info(f"\n{classification_report(y_test, test_preds, target_names=['down', 'up'])}")

    if auc > 0.55:
        logger.info(f"PASS: AUC={auc:.4f} > 0.55 — model learning signal confirmed")
    else:
        logger.warning(f"WARN: AUC={auc:.4f} — model may need more data/features")

    pg.close()
    logger.info("Quick validation complete!")


if __name__ == "__main__":
    main()
