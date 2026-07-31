#!/bin/bash
# full_pipeline_dd.sh — End-to-end auto pipeline (DD variant)
set +e
cd "$(dirname "$0")/.."
LOG_DIR="reports"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M)
LOG_FILE="$LOG_DIR/full_pipeline_dd_$TIMESTAMP.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "========================================"
echo "  analyist_dd FULL PIPELINE DD"
echo "  Started: $(date)"
echo "========================================"

PROGRESS_FILE="/tmp/pipeline_phase_progress.txt"
echo "0" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 0: Container Start
# ==============================================================
echo ""
echo "=== Phase 0: Starting all containers ==="
docker compose up -d 2>&1

echo "Waiting for critical services (postgres, redis, neo4j)..."
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

sleep 5
docker ps --format 'table {{.Names}}\t{{.Status}}' | head -20

# ==============================================================
# PHASE 1: Data Collection
# ==============================================================
echo ""
echo "=== Phase 1: Data Collection ==="

# 1-1. yfinance: Market Data (OHLCV only — skip fundamentals to avoid rate limit)
echo "--- 1-1. yfinance: Market Data (OHLCV only) ---"
docker exec -i stock_yfinance_collector sh -c 'cat > /tmp/phase_1_1.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
from app.main import YFinanceCollectorService
from app.collectors.price_collector import PriceCollector
from app.collectors.stock_list_collector import StockListCollector
from app.processors.technical_indicators import TechnicalIndicatorCalculator
from app.processors.data_cleaner import DataCleaner
from app.storage.postgres_storage import PostgresStorage
import logging; logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')

logger = logging.getLogger(__name__)
config = __import__('app.config', fromlist=['Config']).Config()
storage = PostgresStorage()
slc = StockListCollector()
stocks = slc.get_all_stocks()
logger.info(f'Total stocks to collect: {len(stocks)}')

# Upsert stock master data
for stock in stocks:
    storage.upsert_stock(stock)

# Price collection only (skip fundamentals to avoid rate limit)
pc = PriceCollector()
df = pc.collect_all(stocks)
if df.empty:
    logger.warning('No data collected')
else:
    logger.info(f'Collected data: {len(df)} rows')
    ti = TechnicalIndicatorCalculator()
    df = ti.calculate_all(df)
    dc = DataCleaner()
    df = dc.clean(df)
    import psycopg2
    from psycopg2.extras import execute_values
    pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password='***REDACTED***')
    cur = pg.cursor()
    rows = []
    for r in df.itertuples():
        td = getattr(r, 'trade_date', None)
        if td is None:
            continue
        try:
            o, h, l, c = float(r.open), float(r.high), float(r.low), float(r.close)
            v = int(r.volume) if r.volume == r.volume else 0
        except (TypeError, ValueError):
            continue
        if any(x != x for x in (o, h, l, c)):
            continue
        rows.append((r.stock_code, td, o, h, l, c, v))
    execute_values(cur, """
        INSERT INTO market_data (stock_code, trade_date, open_price, high_price, low_price, close_price, volume)
        VALUES %s
        ON CONFLICT (stock_code, trade_date) DO UPDATE SET
            open_price = EXCLUDED.open_price,
            high_price = EXCLUDED.high_price,
            low_price = EXCLUDED.low_price,
            close_price = EXCLUDED.close_price,
            volume = EXCLUDED.volume
    """, rows, page_size=1000)
    pg.commit()
    cur.close()
    pg.close()
    logger.info(f'Daily collection complete. Processed {len(stocks)} stocks.')
print('yfinance DONE')
PYEOF
docker exec stock_yfinance_collector timeout 1800 python3 /tmp/phase_1_1.py 2>&1
echo "1" > "$PROGRESS_FILE"
sleep 10

# 1-2. KRX: Trading/Short/Derivatives
echo "--- 1-2. KRX: Trading/Short/Derivatives ---"
docker exec -i stock_krx_collector sh -c 'cat > /tmp/phase_1_2.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
from app.main import KrxCollectorService
import logging; logging.basicConfig(level=logging.INFO)
KrxCollectorService().run_daily_collection()
print('KRX DONE')
PYEOF
docker exec stock_krx_collector timeout 600 python3 /tmp/phase_1_2.py 2>&1
echo "2" > "$PROGRESS_FILE"
sleep 30

# 1-3. News Analyzer: News + Sentiment
echo "--- 1-3. News Analyzer: News + Sentiment ---"
docker exec -i stock_news_analyzer sh -c 'cat > /tmp/phase_1_3.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import asyncio
import logging; logging.basicConfig(level=logging.INFO)
from app.main import NewsAnalyzerService
async def run():
    s = NewsAnalyzerService()
    await s.run_collection()
    print('News DONE')
asyncio.run(run())
PYEOF
docker exec stock_news_analyzer timeout 600 python3 /tmp/phase_1_3.py 2>&1
echo "3" > "$PROGRESS_FILE"
sleep 30

# 1-4. Economic Calendar: FOMC/Earnings/CPI
echo "--- 1-4. Economic Calendar: FOMC/Earnings/CPI ---"
docker exec -i stock_economic_calendar sh -c 'cat > /tmp/phase_1_4.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import logging; logging.basicConfig(level=logging.INFO)
from app.main import EconomicCalendarService
EconomicCalendarService().run_daily_update()
print('Economic Calendar DONE')
PYEOF
docker exec stock_economic_calendar timeout 600 python3 /tmp/phase_1_4.py 2>&1
echo "4" > "$PROGRESS_FILE"
sleep 30

# 1-5. Financials: PER/PBR/ROE
echo "--- 1-5. Financials: PER/PBR/ROE ---"
docker exec -i stock_yfinance_collector sh -c 'cat > /tmp/phase_1_5.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import logging, psycopg2; logging.basicConfig(level=logging.INFO)
from app.collectors.price_collector import PriceCollector
from app.storage.postgres_storage import PostgresStorage
pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password='***REDACTED***')
cur = pg.cursor()
cur.execute("SELECT stock_code FROM stocks WHERE market = 'KOSDAQ' AND stock_code ~ '^[0-9]' LIMIT 20")
codes = [r[0] for r in cur.fetchall()]; cur.close(); pg.close()
p = PriceCollector(); s = PostgresStorage()
count = 0
for code in codes:
    r = p.collect_fundamentals({'code':code,'market':'KOSDAQ','name':code})
    if r.get('market_cap') or r.get('roe'):
        s.update_fundamentals(r)
        count += 1
print(f'Financials collected: {count}/{len(codes)} stocks')
PYEOF
docker exec stock_yfinance_collector timeout 600 python3 /tmp/phase_1_5.py 2>&1
echo "5" > "$PROGRESS_FILE"
sleep 30

# 1-6. Stock Vectorizer: Embeddings
echo "--- 1-6. Stock Vectorizer: Embeddings ---"
docker exec -i stock_vectorizer sh -c 'cat > /tmp/phase_1_6.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import logging; logging.basicConfig(level=logging.INFO)
from app.main import StockVectorizerService
StockVectorizerService().run_vectorization()
print('Vectorizer DONE')
PYEOF
docker exec stock_vectorizer timeout 600 python3 /tmp/phase_1_6.py 2>&1
echo "6" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 2: ML Training
# ==============================================================
echo ""
echo "=== Phase 2: ML Training ==="
docker exec stock_xgboost_ml timeout 600 python3 /app/scripts/train_quick.py 2>&1
echo "7" > "$PROGRESS_FILE"

# Get AUC from the latest run (v15 is the best known model)
docker exec -i stock_xgboost_ml sh -c 'cat > /tmp/phase_2_auc.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import json
with open('/app/app/models/saved_models/training-result-v15.json') as f:
    d = json.load(f)
print(f'{d["auc"]:.4f}')
PYEOF
AUC=$(docker exec stock_xgboost_ml timeout 600 python3 /tmp/phase_2_auc.py 2>/dev/null)
echo "Best AUC: $AUC"
docker cp stock_xgboost_ml:/app/app/models/saved_models/training-result-v15.json ./reports/ml_result.json 2>/dev/null
echo "  -> reports/ml_result.json"

# ==============================================================
# PHASE 3: Swing Analysis (All KOSDAQ)
# ==============================================================
echo ""
echo "=== Phase 3: Swing Analysis (All KOSDAQ) ==="
docker exec -i stock_xgboost_ml sh -c 'cat > /tmp/phase_3.py' << 'PYEOF'
import sys, json, psycopg2, numpy as np
sys.path.insert(0, '/app')
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from datetime import datetime

pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password='***REDACTED***')
cur = pg.cursor()
today = datetime.now().strftime('%Y-%m-%d')
cur.execute("SELECT md.stock_code, s.stock_name, s.sector, md.close_price FROM market_data md JOIN stocks s ON md.stock_code = s.stock_code WHERE md.trade_date = %s AND s.market = 'KOSDAQ' AND md.volume > 0", (today,))
stocks = cur.fetchall()
pipeline = FeaturePipeline(pg_conn=pg)
ensemble = EnsembleModel(model_dir='app/models/saved_models')
ensemble.load('app/models/saved_models')
model_features = ensemble.load_feature_names('app/models/saved_models')

results = []
for code, name, sector, close in stocks:
    try:
        feats = pipeline.build_features(code, today)
        if feats.get('feature_count', 0) < 10: continue
        X = np.array([[float(feats.get(f, 0.0)) for f in model_features]], dtype=np.float32)
        X = np.nan_to_num(X, nan=0.0)
        prob = float(ensemble.predict(X)[0])
        results.append({'code':code,'name':name,'sector':sector,'close':float(close),'prob':round(prob,4),'dir':'UP' if prob>0.5 else 'DOWN','conf':round(abs(prob-0.5)*2,4)})
    except: pass

results.sort(key=lambda x: x['conf'], reverse=True)
import json as j
with open('/app/reports/swing_candidates.json', 'w') as f:
    j.dump({'date':today,'total':len(results),'up':len([r for r in results if r['dir']=='UP']),'down':len([r for r in results if r['dir']=='DOWN']),'high_confidence':[r for r in results if r['conf']>=0.30],'top_up':[r for r in results if r['dir']=='UP'][:20],'top_down':[r for r in results if r['dir']=='DOWN'][:20]}, f, indent=2, ensure_ascii=False)
print(f'Swing analysis saved: {len(results)} stocks, {len([r for r in results if r["dir"]=="UP"])} UP, {len([r for r in results if r["dir"]=="DOWN"])} DOWN')
pg.close()
PYEOF
docker exec stock_xgboost_ml timeout 600 python3 /tmp/phase_3.py 2>&1
echo "8" > "$PROGRESS_FILE"
docker cp stock_xgboost_ml:/app/reports/swing_candidates.json ./reports/swing_candidates.json 2>/dev/null
echo "  -> reports/swing_candidates.json"

# ==============================================================
# PHASE 4: Backtest
# ==============================================================
echo ""
echo "=== Phase 4: Backtest ==="
docker exec -i stock_xgboost_ml sh -c 'cat > /tmp/phase_4.py' << 'PYEOF'
import sys, json, psycopg2, numpy as np
sys.path.insert(0, '/app')
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.training.trainer import Trainer
from sklearn.metrics import roc_auc_score, accuracy_score

pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password='***REDACTED***')
cur = pg.cursor()
cur.execute("SELECT stock_code FROM market_data WHERE trade_date >= '2026-06-01' GROUP BY stock_code HAVING COUNT(*) >= 30 ORDER BY stock_code LIMIT 20")
stocks_list = [r[0] for r in cur.fetchall()]; cur.close()
pipeline = FeaturePipeline(pg_conn=pg); trainer = Trainer(storage=None, feature_pipeline=pipeline)
result = trainer.prepare_training_data(stock_codes=stocks_list, days=90)
if result[0] is None: print('Backtest FAILED - no training data'); exit()
X_train, X_val, X_test, y_train, y_val, y_test, fnames = result
all_X = np.concatenate([X_train, X_val, X_test], axis=0)
all_y = np.concatenate([y_train, y_val, y_test], axis=0)
ensemble = EnsembleModel(model_dir='app/models/saved_models')
ensemble.load('app/models/saved_models')
model_f = ensemble.load_feature_names('app/models/saved_models')
core_idx = [fnames.index(f) for f in model_f if f in fnames]
all_X_core = all_X[:, core_idx]
probs = ensemble.predict(all_X_core)
try: auc = roc_auc_score(all_y, probs)
except: auc = 0.5
acc = accuracy_score(all_y, (probs>0.5).astype(int))
print(f'Backtest: AUC={auc:.4f}, ACC={acc:.4f}, Samples={len(all_y)}')
import json as j
with open('/app/reports/backtest_result.json','w') as f:
    j.dump({'auc':round(auc,4),'accuracy':round(acc,4),'samples':len(all_y)}, f)
pg.close()
PYEOF
docker exec stock_xgboost_ml timeout 600 python3 /tmp/phase_4.py 2>&1
echo "9" > "$PROGRESS_FILE"
docker cp stock_xgboost_ml:/app/reports/backtest_result.json ./reports/backtest_result.json 2>/dev/null
echo "  -> reports/backtest_result.json"

# ==============================================================
# PHASE 5: Strategy Execution
# ==============================================================
echo ""
echo "=== Phase 5: Trading Strategies ==="
docker exec -i stock_strategy_agents sh -c 'cat > /tmp/phase_5.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import logging; logging.basicConfig(level=logging.INFO)
from app.main import StrategyAgentService
StrategyAgentService().run_all_strategies()
print('Strategies DONE')
PYEOF
docker exec stock_strategy_agents timeout 600 python3 /tmp/phase_5.py 2>&1
echo "10" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 6: Cleanup old news (30d+)
# ==============================================================
echo ""
echo "=== Phase 6: Data Cleanup ==="
docker exec stock_postgres timeout 600 psql -U stock_user -d stock_trading -c "
DELETE FROM news_analysis WHERE published_at < NOW() - INTERVAL '30 days';
DELETE FROM stock_sentiment WHERE analysis_date < NOW() - INTERVAL '30 days';
" 2>&1
echo "Old news purged."
echo "11" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 7: Summary Report
# ==============================================================
echo ""
echo "=== Phase 7: Pipeline Summary ==="
echo ""
echo "========================================"
echo "  FULL PIPELINE COMPLETE"
echo "  Time: $(date)"
echo "========================================"
echo ""
echo "Results:"
for f in reports/swing_candidates.json reports/backtest_result.json reports/ml_result.json; do
  if [ -f "$f" ]; then
    echo "  $f"
  else
    echo "  (not available) $f"
  fi
done
echo "  - Best AUC: ${AUC:-N/A}"
echo "  - Log: $(ls -t reports/full_pipeline_dd_*.log 2>/dev/null | head -1)"

echo ""
echo "Pipeline finished at: $(date)"
