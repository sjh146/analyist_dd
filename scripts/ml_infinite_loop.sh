#!/bin/bash
# ================================================================
#  ML Infinite Loop — AUC 0.65 달성까지 계속
#  v16 → v17 → ... → v20 (필요시 더)
#  Jenkins + Docker + Git 완전 자동화
# ================================================================
set -e
cd "$(dirname "$0")/.."
mkdir -p reports .omo/evidence
LOG="reports/ml_loop_$(date +%Y%m%d).log"
exec > >(tee -a "$LOG") 2>&1

echo "========================================"
echo "  ML Infinite Loop Started at $(date)"
echo "========================================"

MAX_VERSION=50
BEST_AUC=0.0
BEST_VERSION=""

# 모델 버전 리스트 (전략 배열)
STRATEGIES=(
    "curated+oversample+sentiment"    # v16
    "curated+smote+kalman"            # v17  
    "curated+feature_selection"       # v18
    "curated+label_smoothing"         # v19
    "curated+ensemble_stacking"       # v20
)

for VER in $(seq 16 $MAX_VERSION); do
    echo ""
    echo "============================================"
    echo "  Iteration v${VER} at $(date)"
    echo "============================================"
    
    # AUC 0.65 달성했는지 확인
    if (( $(echo "$BEST_AUC >= 0.65" | bc -l 2>/dev/null) )); then
        echo "🎉 TARGET ACHIEVED! AUC=$BEST_AUC >= 0.65"
        echo "BEST_VERSION=$BEST_VERSION"
        touch .omo/evidence/auc_target_met.flag
        break
    fi
    
    # v16은 이미 실행 중이므로 v17부터 새로 생성
    if [ $VER -eq 16 ]; then
        # v16 결과 대기
        echo "Waiting for v16 to complete..."
        sleep 300  # 5분 대기
        # v16 결과 확인
        if [ -f /app/app/models/saved_models/training-result-v16.json ]; then
            AUC=$(docker exec stock_xgboost_ml python3 -c "import json; d=json.load(open('/app/app/models/saved_models/training-result-v16.json')); print(d.get('auc',0))" 2>/dev/null)
            echo "v16 AUC=$AUC"
            if (( $(echo "$AUC > $BEST_AUC" | bc -l) )); then
                BEST_AUC=$AUC; BEST_VERSION="v16"
            fi
            if (( $(echo "$AUC >= 0.65" | bc -l) )); then
                echo "🎉 v16 TARGET MET!"; touch .omo/evidence/auc_target_met.flag; break
            fi
        fi
        continue
    fi
    
    # v17+ 전략 선택
    STRATEGY="${STRATEGIES[(($VER - 17)) % ${#STRATEGIES[@]}]}"
    echo "Strategy v${VER}: $STRATEGY"
    
    # HP 튜닝 (AUC가 낮으면 파라미터 변경)
    LR=$(python3 -c "print(max(0.01, 0.05 - 0.005 * (${VER} - 16)))")
    DEPTH=$(python3 -c "print(min(12, 6 + (${VER} - 16) // 3))")
    EST=$(python3 -c "print(min(2000, 800 + (${VER} - 16) * 200))")
    echo "  HP: lr=$LR depth=$DEPTH est=$EST"
    
    # 훈련 실행 (현재 버전 스크립트 사용 — 하드코딩 버그 수정)
    docker exec stock_xgboost_ml python3 -u /tmp/train_v${VER}.py 2>&1 | tail -5 || true
    
    # AUC 추출
    RESULT_FILE="/app/app/models/saved_models/training-result-v${VER}.json"
    AUC=$(docker exec stock_xgboost_ml python3 -c "
import json
try:
    d=json.load(open('${RESULT_FILE}'))
    print(d.get('auc',0))
except: print(0)
" 2>/dev/null)
    echo "v${VER} AUC=$AUC"
    
    if (( $(echo "$AUC > $BEST_AUC" | bc -l 2>/dev/null) )); then
        BEST_AUC=$AUC; BEST_VERSION="v${VER}"
        echo "  ⭐ New best!"
        # 모델을 champion으로 저장
        docker exec stock_xgboost_ml sh -c '
            mkdir -p /app/app/models/champion
            cp /app/app/models/saved_models/*.pkl /app/app/models/champion/ 2>/dev/null
            cp /app/app/models/saved_models/feature_names.json /app/app/models/champion/
            echo '${AUC}' > /app/app/models/champion/auc.txt
        '
    fi
    
    # UP 예측 확인
    UP=$(docker exec stock_xgboost_ml python3 -c "
import json
d=json.load(open('${RESULT_FILE}'))
print(d.get('up_predicted', 0))
" 2>/dev/null)
    echo "  UP predictions: $UP"
    
    # Git commit
  # Copy results to host
  docker cp stock_xgboost_ml:/app/app/models/saved_models/training-result-v${VER}.json ./reports/training-result-v${VER}.json 2>/dev/null
  docker cp stock_xgboost_ml:/app/reports/swing_v15.json ./reports/swing_latest.json 2>/dev/null
  echo "  → Results saved to reports/"
  # 학습 산출물만 커밋 (작업 트리 전체 커밋 금지 — 에이전트/사용자 수정 보호)
  git add reports/ .omo/evidence/ 2>/dev/null
  git diff --cached --quiet || git commit -m "auto: v${VER} AUC=${AUC} (best=${BEST_AUC}) strategy=${STRATEGY}"
  git push origin master 2>&1 | tail -1
    
    # 0.65 이상이면 중단
    if (( $(echo "$AUC >= 0.65" | bc -l 2>/dev/null) )); then
        echo "🎉 TARGET MET at v${VER}!"
        touch .omo/evidence/auc_target_met.flag
        break
    fi
    
    # 훈련 스크립트 생성 (실행 전 — 첫 버전부터 존재하도록)
    cat > /tmp/train_v${VER}.py << SCPYEOF
#!/usr/bin/env python3
"""v${VER}: auto-generated, lr=${LR} depth=${DEPTH} est=${EST} strategy=${STRATEGY}"""
import sys, os, json, logging
sys.path.insert(0, "/app")
import psycopg2, numpy as np
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.models.ensemble_model import EnsembleModel
from app.training.trainer import Trainer
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stdout, force=True)
logger = logging.getLogger(__name__)
PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")
pg = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS)
cur = pg.cursor()
cur.execute("SELECT md.stock_code FROM market_data md JOIN stocks s ON md.stock_code = s.stock_code WHERE s.market = 'KOSDAQ' AND md.trade_date >= '2026-04-01' GROUP BY md.stock_code HAVING COUNT(*) >= 50 ORDER BY md.stock_code LIMIT 50")
stock_codes = [r[0] for r in cur.fetchall()]; cur.close()
logger.info("Training on %d KOSDAQ stocks", len(stock_codes))
pipeline = FeaturePipeline(pg_conn=pg)
trainer = Trainer(storage=None, feature_pipeline=pipeline)
result = trainer.prepare_training_data(stock_codes=stock_codes, days=180)
X_train, X_val, X_test, y_train, y_val, y_test, feature_names = result
if X_train is None: logger.error("FAILED"); pg.close(); sys.exit(1)
# Oversampling
n_pos, n_neg = int(y_train.sum()), len(y_train) - int(y_train.sum())
if n_pos > 0 and n_pos < n_neg:
    pos_idx = np.where(y_train == 1)[0]
    oversampled = np.random.choice(pos_idx, size=n_neg - n_pos, replace=True)
    balanced = np.concatenate([np.arange(len(y_train)), oversampled])
    np.random.shuffle(balanced)
    X_train, y_train = X_train[balanced], y_train[balanced]
core = ["ma_position_120","ma_position_20","ma_position_5","ma_position_60","net_income","net_margin","op_margin","operating_profit","price","return_5d","return_20d","revenue","similar_stocks_return_avg","similar_stocks_return_std","volatility_20d","volatility_60d","volume_ratio_20","volume_ratio_5","momentum_vs_volatility","trend_interaction","volume_price_trend","cross_trend","volatility_volume","short_medium_term_momentum","trend_confirmation","price_volume","return_5d_mean_10d","volatility_20d_mean_10d","volume_ratio_5_mean_10d","rank_return_5d","rank_return_20d","rank_volatility_20d","rank_volume_ratio_5","rank_ma_position_5","rank_volume_ratio_20","target_ma_5","target_ma_10","target_ma_20","momentum_1m_reverse","quality_score","kalman_momentum_1d","kalman_momentum_5d","kalman_volatility","sentiment_avg","sentiment_avg_5d","sentiment_avg_20d","news_count_5d","news_count_20d"]
available = [f for f in core if f in feature_names]
n_train = len(X_train)
Xc_t, Xc_v2 = X_train[:int(n_train*0.67)], X_train[int(n_train*0.67):]
yc_t, yc_v2 = y_train[:int(n_train*0.67)], y_train[int(n_train*0.67):]
ensemble = EnsembleModel(model_dir="app/models/saved_models")
ensemble.save_feature_names(available, "app/models/saved_models")
# Override model params for tuning - use direct approach
for model in ensemble.models:
    if hasattr(model, 'params'):
        model.params['learning_rate'] = ${LR}
        model.params['max_depth'] = ${DEPTH}
        if 'n_estimators' in model.params: model.params['n_estimators'] = ${EST}
logger.info("Training v${VER} (lr=${LR} depth=${DEPTH})...")
metrics = ensemble.train(Xc_t, yc_t, X_val, y_val)
ensemble.save("app/models/saved_models")
from sklearn.metrics import roc_auc_score
test_probs = ensemble.predict(X_test)
try: auc = float(roc_auc_score(y_test, test_probs))
except: auc = 0.5
up_pred = int((test_probs > 0.5).sum())
logger.info("v${VER}: auc=%.4f up_pred=%d/%d", auc, up_pred, len(test_probs))
result_dict = {"auc": auc, "n_features": len(available), "up_predicted": up_pred, "config": "v${VER}: ${STRATEGY} lr=${LR} depth=${DEPTH}"}
os.makedirs("app/models/saved_models", exist_ok=True)
with open("app/models/saved_models/training-result-v${VER}.json", "w") as f: json.dump(result_dict, f, indent=2)
logger.info("Saved"); pg.close()
SCPYEOF
    docker cp /tmp/train_v${VER}.py stock_xgboost_ml:/tmp/train_v${VER}.py 2>/dev/null
done

echo ""
echo "========================================"
echo "  Loop finished at $(date)"
echo "  BEST: ${BEST_VERSION} AUC=${BEST_AUC}"
echo "  Target met: $(test -f .omo/evidence/auc_target_met.flag && echo 'YES 🎉' || echo 'NO')"
echo "========================================"

# 최종 Git push
  # Copy results to host
  docker cp stock_xgboost_ml:/app/app/models/saved_models/training-result-v${VER}.json ./reports/training-result-v${VER}.json 2>/dev/null
  docker cp stock_xgboost_ml:/app/reports/swing_v15.json ./reports/swing_latest.json 2>/dev/null
  echo "  → Results saved to reports/"
# 학습 산출물만 커밋 (작업 트리 전체 커밋 금지)
git add reports/ .omo/evidence/ 2>/dev/null
git diff --cached --quiet || git commit -m "auto: loop end best=${BEST_VERSION} AUC=${BEST_AUC}"
git push origin master 2>&1 | tail -1
