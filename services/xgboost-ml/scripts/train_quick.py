#!/usr/bin/env python3
"""Quick validation: train on KOSDAQ stocks to verify AUC > 0.5."""
import sys, os, logging, json
sys.path.insert(0, '/app')

import psycopg2, numpy as np
from datetime import datetime, timedelta

from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.training.trainer import Trainer

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "***REDACTED***")

def get_pg_conn():
    return psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS)

def get_training_stocks(pg_conn, n=50):
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
    stock_codes = get_training_stocks(pg, n=50)
    logger.info(f"Training on {len(stock_codes)} KOSDAQ stocks")

    pipeline = FeaturePipeline(pg_conn=pg)
    ensemble = EnsembleModel(model_dir="app/models/saved_models")
    trainer = Trainer(storage=None, feature_pipeline=pipeline)

    logger.info("Preparing training data (180 days)...")
    result = trainer.prepare_training_data(stock_codes=stock_codes, days=180)
    X_train, X_val, X_test, y_train, y_val, y_test, feature_names = result

    if X_train is None:
        logger.error("Training data preparation failed!")
        return

    n_features = X_train.shape[1]
    logger.info(f"Data ready: {len(X_train)} train, {len(X_val)} val, {len(X_test)} test, {n_features} features")

    model_dir = "app/models/saved_models"
    ensemble.save_feature_names(feature_names, model_dir)
    logger.info(f"Saved {len(feature_names)} feature names")

    logger.info("Training ensemble...")
    metrics = ensemble.train(X_train, y_train, X_val, y_val)
    logger.info(f"Training metrics: {json.dumps(metrics, indent=2, default=str)}")

    ensemble.save(model_dir)

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
    logger.info(f"\n{classification_report(y_test, test_preds, target_names=['down', 'up'], labels=[0, 1], zero_division=0)}")

    if auc > 0.55:
        logger.info(f"PASS: AUC={auc:.4f} > 0.55")
    else:
        logger.warning(f"WARN: AUC={auc:.4f}")

    result_dict = {
        "auc": auc, "accuracy": accuracy, "f1": f1,
        "n_stocks": len(stock_codes), "n_features": n_features,
        "n_train": len(X_train), "n_val": len(X_val), "n_test": len(X_test),
        "feature_names": feature_names
    }
    os.makedirs(".omo/evidence", exist_ok=True)
    with open(".omo/evidence/training-result.json", "w") as f:
        json.dump(result_dict, f, indent=2)
    logger.info("Training result saved")
    pg.close()
    logger.info("Done!")

if __name__ == "__main__":
    main()
