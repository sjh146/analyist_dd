#!/bin/bash
# full_pipeline_dd.sh — End-to-end auto pipeline (DD variant)
set +e
cd "$(dirname "$0")/.."
LOG_DIR="reports"
mkdir -p "$LOG_DIR"

# ── 로그 로테이션: 실행 전 오래된/초과 로그 자동 정리 ──────────────
# 1) 14일 이상 된 파이프라인 로그 삭제
find "$LOG_DIR" -maxdepth 1 -type f -name "full_pipeline_dd_*.log" -mtime +14 -delete 2>/dev/null
# 2) 최근 15개만 유지 (초과분 삭제)
ls -1t "$LOG_DIR"/full_pipeline_dd_*.log 2>/dev/null | tail -n +16 | xargs -r rm -f 2>/dev/null
# 3) cron 로그 1MB 초과 시 최근 1000줄만 유지
for f in "$LOG_DIR"/cron_train.log "$LOG_DIR"/cron_ml_loop.log "$LOG_DIR"/news_cleanup_cron.log .omo/evidence/swing-pipeline-cron.log; do
  if [ -f "$f" ] && [ "$(stat -c%s "$f" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    tail -n 1000 "$f" > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f" 2>/dev/null
  fi
done

TIMESTAMP=$(date +%Y%m%d_%H%M)
LOG_FILE="$LOG_DIR/full_pipeline_dd_$TIMESTAMP.log"
# Log to file always; mirror to stdout ONLY when running in foreground (TTY)
if [ -t 1 ]; then
    exec > >(tee -a "$LOG_FILE") 2>&1
else
    exec >> "$LOG_FILE" 2>&1
fi

# run_docker_phase — run a python script inside a container, writing stdout to a
# plain file (O_APPEND) instead of the pipeline's stdout. High-volume output never
# blocks on a pipe buffer, and < /dev/null avoids any stdin wait.
run_docker_phase() {
    local container="$1"
    local script="$2"
    local timeout="${3:-3600}"
    local phase_log="$LOG_DIR/$(basename "$script" .py)_${TIMESTAMP}.log"
    docker exec "$container" timeout "$timeout" python3 "$script" >> "$phase_log" 2>&1 < /dev/null
    local rc=$?
    if [ -f "$phase_log" ]; then
        cat "$phase_log" >> "$LOG_FILE"
        rm -f "$phase_log"
    fi
    return $rc
}

# run_docker_script — like run_docker_phase, for an already-present in-container script.
run_docker_script() {
    local container="$1"
    local script="$2"
    local timeout="${3:-1800}"
    local phase_log="$LOG_DIR/$(basename "$script" .py)_${TIMESTAMP}.log"
    docker exec "$container" timeout "$timeout" python3 "$script" >> "$phase_log" 2>&1 < /dev/null
    local rc=$?
    if [ -f "$phase_log" ]; then
        cat "$phase_log" >> "$LOG_FILE"
        rm -f "$phase_log"
    fi
    return $rc
}

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
# --no-build: 파이프라인 실행마다 이미지 빌드(CUDA 등 대용량 다운로드) 방지.
# 파이프라인 필수 서비스만 기동 (jenkins/grafana/prometheus 등은 호스트에서
# 별도 실행 중이거나 불필요 — 포트 충돌 방지).
CORE_SERVICES="postgres redis neo4j krx-collector yfinance-collector economic-calendar news-analyzer stock-vectorizer xgboost-ml strategy-agents api-gateway"
docker compose up -d --no-build $CORE_SERVICES 2>&1 | tail -5

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
logging.raiseExceptions = False

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
    import os, psycopg2
    from psycopg2.extras import execute_values
    pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password=os.environ.get('POSTGRES_PASSWORD',''))
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
    # 시세 적재 위생: 이 보조 수집기(pykrx 레거시 경로)는 공식 경로(KRX OpenAPI / KIS)가
    # 넣은 행을 덮어쓰지 않는다(저장소 계층 postgres_storage.py 와 동일 정책 — 같은 플래그).
    # 기본 = 빈 자리만 채우기(DO NOTHING). 되돌리려면 컨테이너에 YF_MARKET_DATA_OVERWRITE=1.
    yf_overwrite = os.getenv('YF_MARKET_DATA_OVERWRITE', '0').strip().lower() in ('1', 'true', 'yes', 'on')
    conflict = """ON CONFLICT (stock_code, trade_date) DO UPDATE SET
            open_price = EXCLUDED.open_price,
            high_price = EXCLUDED.high_price,
            low_price = EXCLUDED.low_price,
            close_price = EXCLUDED.close_price,
            volume = EXCLUDED.volume""" if yf_overwrite else "ON CONFLICT (stock_code, trade_date) DO NOTHING"
    logger.info('market_data 적재 모드: %s', '덮어쓰기(OVERWRITE=1)' if yf_overwrite else '빈 자리만 채우기(DO NOTHING)')
    execute_values(cur, f"""
        INSERT INTO market_data (stock_code, trade_date, open_price, high_price, low_price, close_price, volume)
        VALUES %s
        {conflict}
    """, rows, page_size=1000)
    pg.commit()
    cur.close()
    pg.close()
    logger.info(f'Daily collection complete. Processed {len(stocks)} stocks.')
print('yfinance DONE')
PYEOF
run_docker_phase stock_yfinance_collector /tmp/phase_1_1.py 3600
echo "1" > "$PROGRESS_FILE"
sleep 10

# 1-2. KRX: Trading/Short/Derivatives
echo "--- 1-2. KRX: Trading/Short/Derivatives ---"
docker exec -i stock_krx_collector sh -c 'cat > /tmp/phase_1_2.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
from app.main import KrxCollectorService
import logging; logging.basicConfig(level=logging.INFO)
logging.raiseExceptions = False
KrxCollectorService().run_daily_collection()
print('KRX DONE')
PYEOF
run_docker_phase stock_krx_collector /tmp/phase_1_2.py 600
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
run_docker_phase stock_news_analyzer /tmp/phase_1_3.py 600
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
run_docker_phase stock_economic_calendar /tmp/phase_1_4.py 600
echo "4" > "$PROGRESS_FILE"
sleep 30

# 1-5. Financials: PER/PBR/ROE
echo "--- 1-5. Financials: PER/PBR/ROE ---"
docker exec -i stock_yfinance_collector sh -c 'cat > /tmp/phase_1_5.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import logging, os, psycopg2; logging.basicConfig(level=logging.INFO)
logging.raiseExceptions = False
from app.collectors.price_collector import PriceCollector
from app.storage.postgres_storage import PostgresStorage
pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password=os.environ.get('POSTGRES_PASSWORD',''))
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
run_docker_phase stock_yfinance_collector /tmp/phase_1_5.py 600
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
run_docker_phase stock_vectorizer /tmp/phase_1_6.py 600
echo "6" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 2: ML Training — 챔피언 재학습(최신 데이터) + 보호된 승격
# ==============================================================
# 왜 바뀌었나: 예전 Phase 2 는 /app/scripts/train_quick.py 를 돌렸는데
#   ① Trainer 반환값이 7개로 늘어 unpack 크래시(2026-09-23), ② AUC 는 제거된
#   saved_models/training-result-v15.json 을 읽어 항상 N/A, ③ 학습 결과를
#   app/models/champion 에 바로 덮어써 10종목 스모크 모델이 라이브 챔피언을
#   대체할 수 있었다. 이제 후보 디렉터리 학습 → AUC 게이트 승격으로 바꾼다.
echo ""
echo "=== Phase 2: ML Training (champion retrain on latest data) ==="
CAND_DIR="app/models/champion_cand"
CHAMP_DIR="app/models/champion"
# 낡은 후보가 남아 있으면 재학습 실패 시 그대로 승격되어 버린다 → 먼저 비운다
docker exec stock_xgboost_ml sh -c "rm -rf /app/$CAND_DIR" >> "$LOG_FILE" 2>&1
# 학습 규모: 최근 데이터 우선 200종목 × 120일(≈1.6만 패널행) ≈ 20~25분
#   (피처 빌드는 종목-일 쌍당 약 0.085초 — 늘릴 때는 timeout 도 함께 올린다)
docker exec stock_xgboost_ml timeout 1800 sh -c \
    "cd /app && python -m app.training.retrain_champion --days 120 --stock-limit 200 --out-dir $CAND_DIR" \
    >> "$LOG_FILE" 2>&1 < /dev/null
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "  챌린저 학습 실패(exit=$RC) — 챔피언 유지, 승격 생략"
else
    # 승격 게이트: 후보 val AUC ≥ 0.55 이고 현 챔피언 이상일 때만 교체 (직전 챔피언은 champion_prev_* 로 백업)
    docker exec stock_xgboost_ml python -m app.training.champion_promote \
        --candidate "$CAND_DIR" --champion "$CHAMP_DIR" \
        --min-auc 0.55 --min-improvement 0.0 \
        --summary-out app/reports/ml_result.json >> "$LOG_FILE" 2>&1 < /dev/null
fi
AUC=$(docker exec stock_xgboost_ml sh -c "cat /app/$CHAMP_DIR/auc.txt" 2>/dev/null | tr -d '\n ')
echo "Best AUC: ${AUC:-N/A} (live champion)"
docker cp stock_xgboost_ml:/app/app/reports/ml_result.json ./reports/ml_result.json 2>/dev/null
echo "  -> reports/ml_result.json"
echo "7" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 3: Swing Analysis (All KOSDAQ)
# ==============================================================
echo ""
echo "=== Phase 3: Swing Analysis (All KOSDAQ) ==="
docker exec -i stock_xgboost_ml sh -c 'cat > /tmp/phase_3.py' << 'PYEOF'
import sys, json, os, psycopg2, numpy as np
sys.path.insert(0, '/app')
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from datetime import datetime

pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password=os.environ.get('POSTGRES_PASSWORD',''))
cur = pg.cursor()
today = datetime.now().strftime('%Y-%m-%d')
cur.execute("SELECT md.stock_code, s.stock_name, s.sector, md.close_price FROM market_data md JOIN stocks s ON md.stock_code = s.stock_code WHERE md.trade_date = %s AND s.market = 'KOSDAQ' AND md.volume > 0 ORDER BY md.stock_code", (today,))
stocks = cur.fetchall()
pipeline = FeaturePipeline(pg_conn=pg)
ensemble = EnsembleModel(model_dir='app/models/champion')
ensemble.load('app/models/champion')
model_features = ensemble.load_feature_names('app/models/champion')

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
run_docker_phase stock_xgboost_ml /tmp/phase_3.py 900
echo "8" > "$PROGRESS_FILE"
docker cp stock_xgboost_ml:/app/reports/swing_candidates.json ./reports/swing_candidates.json 2>/dev/null
echo "  -> reports/swing_candidates.json"

# ==============================================================
# PHASE 4: Backtest
# ==============================================================
echo ""
echo "=== Phase 4: Backtest ==="
docker exec -i stock_xgboost_ml sh -c 'cat > /tmp/phase_4.py' << 'PYEOF'
import sys, json, os, psycopg2, numpy as np
sys.path.insert(0, '/app')
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.training.trainer import Trainer
from sklearn.metrics import roc_auc_score, accuracy_score

pg = psycopg2.connect(host='postgres',port=5432,dbname='stock_trading',user='stock_user',password=os.environ.get('POSTGRES_PASSWORD',''))
# 유니버스: KOSPI 30 + KOSDAQ 20 층화 무작위 (ETF/ETN 제외, seed 42 고정 → 재현 가능)
# 2026-08 수정: 기존 ORDER BY stock_code LIMIT 50 (코드순 편향 + 채권/콩 ETN 다수) 제거
from app.training.universe import select_backtest_universe
stocks_list = select_backtest_universe(pg, n_kospi=30, n_kosdaq=20, min_days=30, seed=42)
print('Backtest universe:', len(stocks_list), 'stocks')
pipeline = FeaturePipeline(pg_conn=pg); trainer = Trainer(storage=None, feature_pipeline=pipeline)
from datetime import datetime, timedelta
_end = datetime.now(); _start = _end - timedelta(days=90)
df = pipeline.build_training_features(stocks_list, _start.strftime('%Y-%m-%d'), _end.strftime('%Y-%m-%d'))
if df is None or len(df) < 100: print('Backtest FAILED - no data'); exit()
if 'date' in df.columns: df = df.sort_values('date').reset_index(drop=True)
y = trainer._create_labels(df)
ensemble = EnsembleModel(model_dir='app/models/champion')
ensemble.load('app/models/champion')
# 챔피언 json 계약 순서 0-fill 행렬 (2026-08: 트레이너 분산 필터가 상수 피처를 제거해
# 폭 불일치 CatBoostError 발생 → prepare_training_data 경유 대신 직접 구성)
model_f = ensemble.load_feature_names('app/models/champion')
X = np.zeros((len(df), len(model_f)), dtype=np.float32)
for _j, _f in enumerate(model_f):
    if _f in df.columns:
        X[:, _j] = np.nan_to_num(df[_f].to_numpy(dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
_valid = ~np.isnan(y); X, y = X[_valid], y[_valid]
probs = ensemble.predict(X)
try: auc = roc_auc_score(y, probs)
except: auc = 0.5
acc = accuracy_score(y, (probs>0.5).astype(int))
print(f'Backtest: AUC={auc:.4f}, ACC={acc:.4f}, Samples={len(y)}, Features={len(model_f)}')
import json as j
with open('/app/reports/backtest_result.json','w') as f:
    j.dump({'auc':round(auc,4),'accuracy':round(acc,4),'samples':len(y),'features':len(model_f)}, f)
pg.close()
PYEOF
run_docker_phase stock_xgboost_ml /tmp/phase_4.py 900
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
run_docker_phase stock_strategy_agents /tmp/phase_5.py 300
echo "10" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 5-2: 강환국 팩터 전략 (하면 된다! 퀀트투자)
# Value/Quality/Momentum/LowVol/MultiFactor 5종 — paper-only
# ==============================================================
echo ""
echo "=== Phase 5-2: 강환국 팩터 전략 (Value/Quality/Momentum/LowVol/MultiFactor) ==="
docker exec -i stock_strategy_agents sh -c 'cat > /tmp/phase_5b.py' << 'PYEOF'
import sys; sys.path.insert(0, '/app')
import json, logging, os
from datetime import datetime
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
from app.main import StrategyAgentService

svc = StrategyAgentService()
stock_names = {s["stock_code"]: s.get("stock_name") for s in svc.pg_storage.get_all_stocks()}
results = {}
factor_strategies = [
    ("value_factor", svc.value_strategy),
    ("quality_factor", svc.quality_strategy),
    ("momentum_factor", svc.momentum_strategy),
    ("lowvol_factor", svc.lowvol_strategy),
    ("multifactor", svc.multifactor_strategy),
]
for name, strategy in factor_strategies:
    try:
        signals = strategy.analyze()
        results[name] = {
            "signals": len(signals),
            "top": [{"stock_code": s.get("stock_code"), "name": stock_names.get(s.get("stock_code")),
                     "confidence": round(float(s.get("confidence", 0)), 4)} for s in signals[:10]]
            if isinstance(signals, list) else [],
        }
        print(f"[{name}] signals={len(signals) if isinstance(signals, list) else '?'}")
        if isinstance(signals, list):
            for s in signals[:5]:
                print(f"    {s.get('stock_code')} {stock_names.get(s.get('stock_code'), '')} conf={s.get('confidence')}")
    except Exception as e:
        results[name] = {"error": str(e)}
        print(f"[{name}] FAILED: {e}")

# 리포트 저장
os.makedirs("/app/reports", exist_ok=True)
out = "/app/reports/factor_strategies_result.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump({"generated_at": datetime.now().isoformat(), "strategies": results},
              f, ensure_ascii=False, indent=2)
print(f"factor strategies result -> {out}")
PYEOF
run_docker_phase stock_strategy_agents /tmp/phase_5b.py 300
echo "10.5" > "$PROGRESS_FILE"

# ==============================================================
# PHASE 6: Cleanup old news (2d+)
# ==============================================================
echo ""
echo "=== Phase 6: Data Cleanup ==="
bash scripts/cleanup_news_data.sh
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
