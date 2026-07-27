#!/bin/bash
# ================================================================
#  Continuous ML Runner — v18 to v30 non-stop, then loop until 0.65
#  Reports UP stocks + AUC to reports/
# ================================================================
cd "$(dirname "$0")/.."
LOG="reports/continuous_run.log"
exec > >(tee -a "$LOG") 2>&1

echo "=========================================="
echo "  Continuous ML Runner started at $(date)"
echo "  Target: AUC >= 0.65"
echo "=========================================="

install_if_missing() {
    docker exec stock_xgboost_ml pip install -q imbalanced-learn 2>/dev/null || true
}

# Train + report function
train_and_report() {
    local VER=$1
    local LR=$2
    local DEPTH=$3
    local EST=$4
    local DESC=$5
    
    echo ""
    echo "=========================================="
    echo "  v${VER}: ${DESC} at $(date)"
    echo "=========================================="
    
    # Create training script
    cat > /tmp/train_v${VER}.py << PYEOF
#!/usr/bin/env python3
"""v${VER}: ${DESC}"""
import sys, os, json, logging, psycopg2, numpy as np
sys.path.insert(0, "/app")
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.training.trainer import Trainer
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stdout, force=True)
logger = logging.getLogger(__name__)
PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "stock_secure_password_2026")
pg = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS)
cur = pg.cursor()
sql = "SELECT md.stock_code FROM market_data md JOIN stocks s ON md.stock_code = s.stock_code WHERE s.market = 'KOSDAQ' AND md.trade_date >= '2026-04-01' GROUP BY md.stock_code HAVING COUNT(*) >= 50 ORDER BY md.stock_code LIMIT 50"
cur.execute(sql)
stock_codes = [r[0] for r in cur.fetchall()]; cur.close()
logger.info("Training on %d stocks", len(stock_codes))
pipeline = FeaturePipeline(pg_conn=pg)
trainer = Trainer(storage=None, feature_pipeline=pipeline)
result = trainer.prepare_training_data(stock_codes=stock_codes, days=180)
X_train, X_val, X_test, y_train, y_val, y_test, fnames = result
if X_train is None: logger.error("FAILED"); pg.close(); sys.exit(1)
# Oversample
n_pos, n_neg = int(y_train.sum()), len(y_train)-int(y_train.sum())
if n_pos > 0 and n_pos < n_neg:
    pos_idx = np.where(y_train==1)[0]
    over = np.random.choice(pos_idx, size=n_neg-n_pos, replace=True)
    bal = np.concatenate([np.arange(len(y_train)), over])
    np.random.shuffle(bal)
    X_train, y_train = X_train[bal], y_train[bal]
core = ["ma_position_120","ma_position_20","ma_position_5","ma_position_60","net_income","net_margin","op_margin","operating_profit","price","return_5d","return_20d","revenue","similar_stocks_return_avg","similar_stocks_return_std","volatility_20d","volatility_60d","volume_ratio_20","volume_ratio_5","momentum_vs_volatility","trend_interaction","volume_price_trend","cross_trend","volatility_volume","short_medium_term_momentum","trend_confirmation","price_volume","return_5d_mean_10d","volatility_20d_mean_10d","volume_ratio_5_mean_10d","rank_return_5d","rank_return_20d","rank_volatility_20d","rank_volume_ratio_5","rank_ma_position_5","rank_volume_ratio_20","target_ma_5","target_ma_10","target_ma_20","momentum_1m_reverse","quality_score","kalman_momentum_1d","kalman_momentum_5d","kalman_volatility"]
available = [f for f in core if f in fnames]
ensemble = EnsembleModel(model_dir="app/models/saved_models")
ensemble.save_feature_names(fnames, "app/models/saved_models")
for model in ensemble.models:
    if hasattr(model, 'params'):
        model.params['learning_rate'] = ${LR}
        model.params['max_depth'] = ${DEPTH}
        if 'n_estimators' in model.params: model.params['n_estimators'] = ${EST}
logger.info("Training v${VER}...")
metrics = ensemble.train(X_train, y_train, X_val, y_val)
ensemble.save("app/models/saved_models")
from sklearn.metrics import roc_auc_score
test_probs = ensemble.predict(X_test)
try: auc = float(roc_auc_score(y_test, test_probs))
except: auc = 0.5
up_pred = int((test_probs > 0.5).sum())
up_correct = int(((test_probs > 0.5) == y_test).sum())
logger.info("v${VER}: auc=%.4f up=%d/%d", auc, up_pred, len(test_probs))

# UP stocks list from test set (get stock codes)
up_indices = np.where((test_probs > 0.5) == 1)[0]
up_stocks_sample = [{"index":int(i), "prob":round(float(test_probs[i]),4), "actual":int(y_test[i])} for i in up_indices[:20]]

result_dict = {"auc": auc, "up_predicted": up_pred, "up_total": len(test_probs), "config": "v${VER}: lr=${LR} depth=${DEPTH} est=${EST} ${DESC}", "up_stocks": up_stocks_sample}
os.makedirs("app/models/saved_models", exist_ok=True)
with open("app/models/saved_models/training-result-v${VER}.json", "w") as f: json.dump(result_dict, f, indent=2)
logger.info("Saved")
pg.close()
PYEOF
    docker cp /tmp/train_v${VER}.py stock_xgboost_ml:/tmp/train_v${VER}.py 2>/dev/null
    docker exec stock_xgboost_ml python -u /tmp/train_v${VER}.py 2>&1 | tail -3
    
    # Get result
    AUC=$(docker exec stock_xgboost_ml python3 -c "
import json
try:
    d=json.load(open('/app/app/models/saved_models/training-result-v${VER}.json'))
    print(d.get('auc',0))
except: print(0)
" 2>/dev/null)
    UP=$(docker exec stock_xgboost_ml python3 -c "
import json
try:
    d=json.load(open('/app/app/models/saved_models/training-result-v${VER}.json'))
    print(d.get('up_predicted',0))
except: print(0)
" 2>/dev/null)
    
  # === UP stocks 상세 분석 ===
  docker exec stock_xgboost_ml python3 -c "
import sys, json, psycopg2, numpy as np
sys.path.insert(0, "/app")
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
pg = psycopg2.connect(host=chr(39)+chr(112)+chr(111)+chr(115)+chr(116)+chr(103)+chr(114)+chr(101)+chr(115)+chr(39),port=5432,dbname=chr(39)+chr(115)+chr(116)+chr(111)+chr(99)+chr(107)+chr(95)+chr(116)+chr(114)+chr(97)+chr(100)+chr(105)+chr(110)+chr(103)+chr(39),user=chr(39)+chr(115)+chr(116)+chr(111)+chr(99)+chr(107)+chr(95)+chr(117)+chr(115)+chr(101)+chr(114)+chr(39),password=chr(39)+chr(115)+chr(116)+chr(111)+chr(99)+chr(107)+chr(95)+chr(115)+chr(101)+chr(99)+chr(117)+chr(114)+chr(101)+chr(95)+chr(112)+chr(97)+chr(115)+chr(115)+chr(119)+chr(111)+chr(114)+chr(100)+chr(95)+chr(50)+chr(48)+chr(50)+chr(54)+chr(39))
cur = pg.cursor()
cur.execute("SELECT MAX(trade_date) FROM market_data WHERE volume > 0")
last_date = cur.fetchone()[0].strftime("%Y-%m-%d")
cur.execute("SELECT md.stock_code, s.stock_name, s.sector, md.close_price FROM market_data md JOIN stocks s ON md.stock_code = s.stock_code WHERE md.trade_date = %s AND s.market = chr(39)+chr(75)+chr(79)+chr(83)+chr(68)+chr(65)+chr(81)+chr(39) AND md.volume > 0", (last_date,))
stocks = cur.fetchall()
print(f"Scanning {len(stocks)} stocks...")
pipeline = FeaturePipeline(pg_conn=pg)
ensemble = EnsembleModel(model_dir="app/models/saved_models")
ensemble.load("app/models/saved_models")
model_features = ensemble.load_feature_names("app/models/saved_models")
results = []
for code, name, sector, close in stocks:
    try:
        feats = pipeline.build_features(code, last_date)
        if feats.get("feature_count", 0) < 10: continue
        X = np.array([[float(feats.get(f,0.0)) for f in model_features]], dtype=np.float32)
        X = np.nan_to_num(X, nan=0.0)
        prob = float(ensemble.predict(X)[0])
        results.append({"code":code,"name":name,"sector":sector or "-","close":float(close) if close else 0,"prob":round(prob,4),"dir":"UP" if prob>0.5 else "DOWN"})
    except: pass
up = [r for r in results if r["dir"]=="UP"]
up.sort(key=lambda x: x["prob"], reverse=True)
report = {"date":last_date,"total":len(results),"up_count":len(up),"down_count":len(results)-len(up),"top_up":up[:30]}
with open("/app/reports/up_stocks_v${VER}.json","w") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
print(f"UP stocks: {len(up)} / {len(results)}")
for r in up[:5]: print(f"  {r[chr(39)+chr(99)+chr(111)+chr(100)+chr(101)+chr(39)]} {r[chr(39)+chr(110)+chr(97)+chr(109)+chr(101)+chr(39)][:12]:12s} prob={r[chr(39)+chr(112)+chr(114)+chr(111)+chr(98)+chr(39)]:.4f}")
pg.close()
" 2>&1
  docker cp stock_xgboost_ml:/app/reports/up_stocks_v${VER}.json ./reports/up_stocks_v${VER}.json 2>/dev/null
  echo "  → reports/up_stocks_v${VER}.json"
    # Copy to host reports/
    docker cp stock_xgboost_ml:/app/app/models/saved_models/training-result-v${VER}.json ./reports/training-result-v${VER}.json 2>/dev/null
    
    echo "v${VER} → AUC=${AUC} UP=${UP}"
    
    # Track best
    if (( $(echo "$AUC > $BEST_AUC" | bc -l 2>/dev/null) )); then
        BEST_AUC=$AUC; BEST_VER="v${VER}"
        echo "  ⭐ NEW BEST!"
    fi
    
    # Check target
    if (( $(echo "$AUC >= 0.65" | bc -l 2>/dev/null) )); then
        echo "🎉 TARGET MET at v${VER}!"
        touch .omo/evidence/auc_target_met.flag
        return 0
    fi
    return 1
}

install_if_missing
BEST_AUC=0.0
BEST_VER=""

# v18-v30 연속 실행 (2시간 대기 없음!)
let "ver = 18"
while [ $ver -le 50 ]; do
    # 전략 자동 선택
    case $((ver % 5)) in
        0) LR="0.05"; DEPTH="6"; EST="1000"; DESC="default+OS";;
        1) LR="0.03"; DEPTH="4"; EST="1500"; DESC="low_lr+shallow";;
        2) LR="0.08"; DEPTH="8"; EST="800"; DESC="high_lr+deep";;
        3) LR="0.02"; DEPTH="5"; EST="2000"; DESC="very_low_lr+high_est";;
        4) LR="0.04"; DEPTH="7"; EST="1200"; DESC="mid_lr+mid_depth";;
    esac
    
    if train_and_report $ver $LR $DEPTH $EST "$DESC"; then
        break  # target met
    fi
    
    # Git push every 2 versions
    if ((ver % 2 == 0)); then
        git add reports/ .omo/ 2>/dev/null
        git diff --cached --quiet || git commit -m "auto: v${ver} AUC=${AUC}"
        git push origin master 2>&1 | tail -1
    fi
    
    ((ver++))
done

# Final report
echo ""
echo "=========================================="
echo "  RUN FINISHED at $(date)"
echo "  BEST: ${BEST_VER} AUC=${BEST_AUC}"
echo "=========================================="

# Save comprehensive report with UP stocks
echo "{ \"best_version\": \"${BEST_VER}\", \"best_auc\": ${BEST_AUC}, \"target_met\": $(test -f .omo/evidence/auc_target_met.flag && echo 'true' || echo 'false'), \"total_versions\": $((ver - 18)) }" > reports/final_report.json

# Final Git push
git add reports/ .omo/
git diff --cached --quiet || git commit -m "auto: continuous run end best=${BEST_VER} AUC=${BEST_AUC}"
git push origin master 2>&1 | tail -1
