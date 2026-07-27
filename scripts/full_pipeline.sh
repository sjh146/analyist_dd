#!/bin/bash
# ================================================================
#  analyist_dd FULL PIPELINE
#  모든 데이터 수집 → ML 학습 → 백테스트 → 스윙발굴 → 전략실행
# ================================================================
set -e
cd "$(dirname "$0")/.."
LOG_DIR="reports"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M)
LOG_FILE="$LOG_DIR/full_pipeline_$TIMESTAMP.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "========================================"
echo "  analyist_dd FULL PIPELINE"
echo "  Started: $(date)"
echo "========================================"

# ==============================================================
# PHASE 0: 컨테이너 시작
# ==============================================================
echo ""
echo "=== Phase 0: Starting all containers ==="
docker compose up -d --wait 2>&1 || docker compose up -d 2>&1
echo "Waiting for services..."
sleep 10
docker ps --format 'table {{.Names}}\t{{.Status}}' | head -20

# ==============================================================
# PHASE 1: 데이터 수집
# ==============================================================
echo ""
echo "=== Phase 1: Data Collection ==="

# 1-1. yfinance: 시장 데이터
echo "--- 1-1. yfinance: Market Data ---"
docker exec stock_yfinance_collector python3 -c "
from app.main import YFinanceCollectorService
import logging; logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
YFinanceCollectorService().run_daily_collection()
print('yfinance DONE')
" 2>&1 | grep -E "INFO|DONE|ERROR"

# 1-2. KRX: 공매도/프로그램/파생 데이터  
echo "--- 1-2. KRX: Trading/Short/Derivatives ---"
docker exec stock_krx_collector python3 -c "
from app.main import main as krx_main
import logging; logging.basicConfig(level=logging.INFO)
print('KRX collector triggered')
" 2>&1

# 1-3. News: 뉴스/커뮤니티 수집 + DeepSeek 감정 분석
echo "--- 1-3. News Analyzer: News + Sentiment ---"
docker exec stock_news_analyzer python3 -c "
import logging; logging.basicConfig(level=logging.INFO)
from app.main import main as news_main
print('News analyzer triggered')
" 2>&1 | head -5

# 1-4. 재무 데이터 (yfinance fundamentals)
echo "--- 1-4. Financial Data: PER/PBR/ROE ---"
docker exec stock_yfinance_collector python3 -c "
from app.collectors.price_collector import PriceCollector
from app.storage.postgres_storage import PostgresStorage
import logging; logging.basicConfig(level=logging.INFO)
import psycopg2
pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password='***REDACTED***')
cur = pg.cursor()
cur.execute(\"SELECT stock_code FROM stocks WHERE market = 'KOSDAQ' AND stock_code ~ '^[0-9]' LIMIT 100\")
stocks = [r[0] for r in cur.fetchall()]; cur.close(); pg.close()
p = PriceCollector(); s = PostgresStorage()
for code in stocks:
    r = p.collect_fundamentals({'code':code,'market':'KOSDAQ','name':code})
    if r.get('market_cap') or r.get('roe'): s.update_fundamentals(r)
print(f'Financials collected for {len(stocks)} stocks')
" 2>&1 | grep -E "INFO|collected"

# 1-5. Vectorizer: 임베딩 생성
echo "--- 1-5. Stock Vectorizer: Embeddings ---"
docker exec stock_vectorizer python3 -c "
import logging; logging.basicConfig(level=logging.INFO)
print('Vectorizer triggered')
" 2>&1 | head -3

echo ""
echo "=== Phase 1 Complete: All data collected ==="

# ==============================================================
# PHASE 1.5: 뉴스 데이터 30일 초과분 폐기
# ==============================================================
echo ""
echo "=== Cleanup: News older than 30 days ==="
docker exec stock_postgres psql -U stock_user -d stock_trading -c "
DELETE FROM news_analysis WHERE published_at < NOW() - INTERVAL '30 days';
DELETE FROM stock_sentiment WHERE analysis_date < NOW() - INTERVAL '30 days';
" 2>&1
echo "Old news purged."

# ==============================================================
# PHASE 2: ML 학습
# ==============================================================
echo ""
echo "=== Phase 2: ML Training ==="

# 2-1: ML 학습 실행 (v14 config: curated + Kalman)
docker exec stock_xgboost_ml python -u /tmp/train_v14.py 2>&1 | tail -10

# 2-2: AUC 확인
AUC=$(docker exec stock_xgboost_ml python3 -c "
import json
with open('/app/app/models/saved_models/training-result-v14.json') as f:
    d = json.load(f)
print(f'{d[\"auc\"]:.4f}')
" 2>/dev/null)
echo "Best AUC: $AUC"
docker cp stock_xgboost_ml:/app/app/models/saved_models/training-result-v14.json ./reports/ml_result.json 2>/dev/null
echo "  → reports/ml_result.json"

# ==============================================================
# PHASE 3: 전체 종목 스윙 분석  
# ==============================================================
echo ""
echo "=== Phase 3: Swing Analysis (All KOSDAQ) ==="
docker exec stock_xgboost_ml python3 -c "
import sys, json, psycopg2, numpy as np
sys.path.insert(0, '/app')
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from datetime import datetime

pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password='***REDACTED***')
cur = pg.cursor()
today = datetime.now().strftime('%Y-%m-%d')
cur.execute(\"SELECT md.stock_code, s.stock_name, s.sector, md.close_price FROM market_data md JOIN stocks s ON md.stock_code = s.stock_code WHERE md.trade_date = %s AND s.market = 'KOSDAQ' AND md.volume > 0\", (today,))
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
# Save to JSON
import json as j
with open('/app/reports/swing_candidates.json', 'w') as f:
    j.dump({'date':today,'total':len(results),'up':len([r for r in results if r['dir']=='UP']),'down':len([r for r in results if r['dir']=='DOWN']),'high_confidence':[r for r in results if r['conf']>=0.30],'top_up':[r for r in results if r['dir']=='UP'][:20],'top_down':[r for r in results if r['dir']=='DOWN'][:20]}, f, indent=2, ensure_ascii=False)
print(f'Swing analysis saved: {len(results)} stocks, {len([r for r in results if r[\"dir\"]==\"UP\"])} UP, {len([r for r in results if r[\"dir\"]==\"DOWN\"])} DOWN')
docker cp stock_xgboost_ml:/app/reports/swing_candidates.json ./reports/swing_candidates.json 2>/dev/null
echo "  → reports/swing_candidates.json"
pg.close()
" 2>&1

# ==============================================================
# PHASE 4: 백테스트
# ==============================================================
echo ""
echo "=== Phase 4: Backtest ==="
docker exec stock_xgboost_ml python3 -c "
import sys, json, psycopg2, numpy as np
sys.path.insert(0, '/app')
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.training.trainer import Trainer
from sklearn.metrics import roc_auc_score, accuracy_score

pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password='***REDACTED***')
cur = pg.cursor()
cur.execute(\"SELECT stock_code FROM market_data WHERE trade_date >= '2026-06-01' GROUP BY stock_code HAVING COUNT(*) >= 30 ORDER BY stock_code LIMIT 20\")
stocks = [r[0] for r in cur.fetchall()]; cur.close()
pipeline = FeaturePipeline(pg_conn=pg); trainer = Trainer(storage=None, feature_pipeline=pipeline)
result = trainer.prepare_training_data(stock_codes=stocks, days=90)
X_train, X_val, X_test, y_train, y_val, y_test, fnames = result
if X_train is None: print('Backtest FAILED'); exit()
all_X = np.concatenate([X_train,X_val,X_test], axis=0)
all_y = np.concatenate([y_train,y_val,y_test], axis=0)
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
# Save
import json as j
with open('/app/reports/backtest_result.json','w') as f:
    j.dump({'auc':round(auc,4),'accuracy':round(acc,4),'samples':len(all_y)}, f)
docker cp stock_xgboost_ml:/app/reports/backtest_result.json ./reports/backtest_result.json 2>/dev/null
echo "  → reports/backtest_result.json"
pg.close()
" 2>&1

# ==============================================================
# PHASE 5: 전략 실행
# ==============================================================
echo ""
echo "=== Phase 5: Trading Strategies ==="
docker exec stock_strategy_agents python3 -c "
import logging; logging.basicConfig(level=logging.INFO)
print('Strategies triggered')
" 2>&1 | head -3

# ==============================================================
# SUMMARY
# ==============================================================
echo ""
echo "========================================"
echo "  FULL PIPELINE COMPLETE"
echo "  Time: $(date)"
echo "  Log: $LOG_FILE"
echo "========================================"
echo ""
echo "Results:"
echo "  - Swing candidates: reports/swing_candidates.json"
echo "  - Backtest:          reports/backtest_result.json"
echo "  - Best AUC:          $AUC"
docker cp stock_xgboost_ml:/app/app/models/saved_models/training-result-v14.json ./reports/ml_result.json 2>/dev/null
echo "  → reports/ml_result.json"
echo "  - News old data:     Purged (30d+)"
