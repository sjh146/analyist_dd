#!/usr/bin/env python3
"""Train XGBoost+LightGBM ensemble on real KOSDAQ data."""

import sys
import os
import logging
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'services', 'xgboost-ml'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'services', 'xgboost-ml'))

import psycopg2
import numpy as np
import pandas as pd
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


def get_training_stocks(pg_conn, n=50):
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


def main():
    pg = get_pg_conn()
    stock_codes = get_training_stocks(pg, n=50)
    logger.info(f"Training on {len(stock_codes)} KOSDAQ stocks")

    pipeline = FeaturePipeline(pg_conn=pg)
    ensemble = EnsembleModel(model_dir="app/models/saved_models")
    trainer = Trainer(storage=None, feature_pipeline=pipeline)

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    logger.info("Preparing training data...")
    result = trainer.prepare_training_data(stock_codes=stock_codes, days=365)
    X_train, X_val, X_test, y_train, y_val, y_test = result

    if X_train is None:
        logger.error("Training data preparation failed!")
        return

    logger.info(f"Training: {len(X_train)} train, {len(X_val)} val, {len(X_test)} test, {X_train.shape[1]} features")

    # Train ensemble
    logger.info("Training ensemble...")
    metrics = ensemble.train(X_train, y_train, X_val, y_val)
    logger.info(f"Training metrics: {json.dumps(metrics, indent=2, default=str)}")

    # Evaluate on test set
    test_probs = ensemble.predict(X_test)
    test_preds = (test_probs > 0.5).astype(int)

    from sklearn.metrics import accuracy_score, roc_auc_score, f1_score, classification_report

    accuracy = accuracy_score(y_test, test_preds)
    f1 = f1_score(y_test, test_preds, zero_division=0)
    try:
        auc = roc_auc_score(y_test, test_probs)
    except ValueError:
        auc = 0.5

    logger.info(f"Test metrics: accuracy={accuracy:.4f}, f1={f1:.4f}, auc={auc:.4f}")
    logger.info(f"\n{classification_report(y_test, test_preds, target_names=['down', 'up'])}")

    if auc > 0.5:
        logger.info(f"PASS: AUC={auc:.4f} > 0.5 — model is learning signal")
    else:
        logger.warning(f"WARN: AUC={auc:.4f} <= 0.5 — model not learning meaningful signal")

    # Save models
    save_dir = "app/models/saved_models"
    os.makedirs(save_dir, exist_ok=True)
    paths = ensemble.save(save_dir)
    logger.info(f"Models saved: {paths}")

    # Save training report
    report = {
        "timestamp": datetime.now().isoformat(),
        "stocks_trained": len(stock_codes),
        "train_size": len(X_train),
        "val_size": len(X_val),
        "test_size": len(X_test),
        "features": X_train.shape[1],
        "metrics": metrics,
        "test_accuracy": float(accuracy),
        "test_f1": float(f1),
        "test_auc": float(auc),
    }
    report_path = os.path.join(save_dir, "training_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"Report saved: {report_path}")

    pg.close()
    logger.info("Training complete!")


if __name__ == "__main__":
    main()
