#!/bin/bash
# Runs full pipeline skipping yfinance rate-limited parts (uses synthetic data)
set +e
cd "$(dirname "$0")/.."
LOG_DIR="reports"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M)
LOG_FILE="$LOG_DIR/pipeline_test_$TIMESTAMP.log"
START_TIME=$(date +%s)

exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Pipeline Test Mode ==="
echo "Started: $(date)"
echo ""

# -------------------------------------------------------
# STEP 0: Start containers & wait for critical services
# -------------------------------------------------------
echo "0. Starting containers..."
docker compose up -d 2>&1

for i in $(seq 1 30); do
    ALL_HEALTHY=true
    for container in stock_postgres stock_redis stock_neo4j; do
        STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null)
        if [ "$STATUS" != "healthy" ]; then
            ALL_HEALTHY=false
            break
        fi
    done
    if [ "$ALL_HEALTHY" = true ]; then
        echo "All critical services healthy."
        break
    fi
    sleep 2
done
sleep 3

# -------------------------------------------------------
# STEP 1: Generate synthetic market data via yfinance --test-mode
# -------------------------------------------------------
echo ""
echo "1. Generating synthetic market data..."
docker exec stock_yfinance_collector python3 -m app.main --test-mode 2>&1 | grep -Ev "^$"
echo "Synthetic data generation complete."

# -------------------------------------------------------
# STEP 2: ML training
# -------------------------------------------------------
echo ""
echo "2. Running ML training..."
docker exec stock_xgboost_ml timeout 300 python /app/scripts/train_quick.py 2>&1 | tail -30

# -------------------------------------------------------
# STEP 3: Backtest / training data pipeline
# -------------------------------------------------------
echo ""
echo "3. Running backtest / training data pipeline..."
docker exec stock_xgboost_ml timeout 300 python3 -c "
import sys, json, psycopg2, numpy as np
sys.path.insert(0, '/app')
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.training.trainer import Trainer

pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password='stock_secure_password_2026')
cur = pg.cursor()
cur.execute(\"SELECT stock_code FROM market_data WHERE trade_date >= '2026-01-01' GROUP BY stock_code HAVING COUNT(*) >= 30 ORDER BY stock_code LIMIT 5\")
stocks = [r[0] for r in cur.fetchall()]
cur.close()
print(f'Stocks with enough data: {stocks}')
pipeline = FeaturePipeline(pg_conn=pg)
trainer = Trainer(storage=None, feature_pipeline=pipeline)
result = trainer.prepare_training_data(stock_codes=stocks if stocks else ['005930'], days=90)
X_train, X_val, X_test, y_train, y_val, y_test, fnames = result
if X_train is not None:
    print(f'Train OK: {len(X_train)} train + {len(X_val)} val + {len(X_test)} test = {len(X_train)+len(X_val)+len(X_test)} total samples, {len(fnames)} features')
else:
    print('Train FAILED — no training data generated')
pg.close()
" 2>&1

# -------------------------------------------------------
# STEP 4: Strategy signals test
# -------------------------------------------------------
echo ""
echo "4. Testing strategy signals..."
docker exec stock_strategy_agents timeout 120 python3 -c "
from app.strategies.theme_strategy import ThemeStrategy
from app.storage.postgres_storage import PostgresStorage
import logging; logging.basicConfig(level=logging.INFO)
pg = PostgresStorage()
ts = ThemeStrategy(pg)
signals = ts.analyze()
print(f'ThemeStrategy: {len(signals)} signals generated')
" 2>&1

# -------------------------------------------------------
# SUMMARY
# -------------------------------------------------------
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
MINS=$((ELAPSED / 60))
SECS=$((ELAPSED % 60))

echo ""
echo "========================================"
echo "  PIPELINE TEST COMPLETE"
echo "  Time: ${MINS}m ${SECS}s"
echo "  Log: $LOG_FILE"
echo "========================================"
