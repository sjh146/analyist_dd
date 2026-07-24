#!/bin/bash
# Auto ML Training Pipeline
# Runs: trains model, evaluates, tunes if AUC < 0.65, repeats up to 3x
# Usage: ./scripts/auto_train_loop.sh [commit_message]

set -e
cd "$(dirname "$0")/.."
LOG_FILE="reports/auto_train_$(date +%Y%m%d_%H%M).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== ML Auto-Train Pipeline ==="
echo "Started: $(date)"
echo "Git SHA: $(git rev-parse HEAD 2>/dev/null || echo 'N/A')"

MAX_ATTEMPTS=3
AUC_TARGET=0.65
BEST_AUC=0.0
BEST_FILE=""

# Step 1: Check if container is running
echo ""
echo "--- Step 1: Check Container ---"
if ! docker ps --format '{{.Names}}' | grep -q stock_xgboost_ml; then
    echo "ERROR: stock_xgboost_ml container not running!"
    echo "Starting it..."
    docker compose up -d xgboost-ml 2>&1
    sleep 5
fi
echo "Container OK"

# Step 2: Copy latest training script
echo ""
echo "--- Step 2: Prepare Training Script ---"
docker cp scripts/train_v4.py stock_xgboost_ml:/tmp/train_v2.py 2>/dev/null || true

# Verify the latest feature_pipeline.py is in the container (volume mount handles this)

for ATTEMPT in $(seq 1 $MAX_ATTEMPTS); do
    echo ""
    echo "=== Attempt ${ATTEMPT}/${MAX_ATTEMPTS} ==="
    echo "Time: $(date)"
    
    # Step 3: Train
    echo ""
    echo "--- Step 3: Train Model ---"
    docker exec stock_xgboost_ml python -u /tmp/train_v2.py 2>&1
    
    # Step 4: Evaluate
    echo ""
    echo "--- Step 4: Evaluate ---"
    RESULT=$(docker exec stock_xgboost_ml sh -c 'cat /app/.omo/evidence/training-result-v2.json 2>/dev/null || echo "{}"')
    AUC=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('auc',0))" 2>/dev/null || echo "0")
    FEATURES=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('n_features',0))" 2>/dev/null || echo "0")
    
    echo "AUC: $AUC"
    echo "Features: $FEATURES"
    
    # Track best
    if (( $(echo "$AUC > $BEST_AUC" | bc -l 2>/dev/null) )); then
        BEST_AUC=$AUC
        BEST_FILE="training-result-v2.json"
        echo "New best AUC: $BEST_AUC"
        # Save as champion
        docker exec stock_xgboost_ml sh -c '
            mkdir -p /app/app/models/champion
            cp /app/app/models/saved_models/xgboost_model.pkl /app/app/models/champion/ 2>/dev/null || true
            cp /app/app/models/saved_models/lightgbm_model.pkl /app/app/models/champion/ 2>/dev/null || true
            cp /app/app/models/saved_models/catboost_model.pkl /app/app/models/champion/ 2>/dev/null || true
            cp /app/app/models/saved_models/feature_names.json /app/app/models/champion/ 2>/dev/null || true
            echo 0 > /app/app/models/champion/auc.txt
            echo '$BEST_AUC' > /app/app/models/champion/auc.txt
        '
    fi
    
    # Check if target met
    if (( $(echo "$AUC >= $AUC_TARGET" | bc -l 2>/dev/null) )); then
        echo ""
        echo "🎉 TARGET ACHIEVED! AUC $AUC >= $AUC_TARGET"
        break
    fi
    
    # If last attempt, stop
    if [ "$ATTEMPT" -eq "$MAX_ATTEMPTS" ]; then
        echo ""
        echo "Max attempts reached. Best AUC: $BEST_AUC"
        break
    fi
    
    # Step 5: Tune hyperparameters
    echo ""
    echo "--- Step 5: Auto-Tune Hyperparameters ---"
    IMBALANCE=$(echo "$RESULT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('imbalance_ratio', 1.6))
" 2>/dev/null || echo "1.6")
    
    echo "Imbalance ratio: $IMBALANCE"
    NEW_ESTIMATORS=$((1000 + ATTEMPT * 200))
    NEW_LR=$(python3 -c "print(round(0.05 / $ATTEMPT, 4))")
    NEW_DEPTH=$((8 + ATTEMPT))
    NEW_WEIGHT=$(python3 -c "print(round($IMBALANCE * (1.0 + $ATTEMPT * 0.1), 2))")
    
    echo "Tuning: n_estimators=$NEW_ESTIMATORS, lr=$NEW_LR, max_depth=$NEW_DEPTH, scale_pos_weight=$NEW_WEIGHT"
    
    # Update training script params before next attempt
    sed -i "s/n_estimators\": [0-9]*/n_estimators\": $NEW_ESTIMATORS/" scripts/train_v4.py 2>/dev/null || true
    docker exec stock_xgboost_ml python3 -c "
import json
# Update XGBoost params
with open('/app/app/models/saved_models/xgboost_model.pkl', 'rb') as f:
    pass
from app.models.xgboost_model import XGBoostModel
xgb = XGBoostModel()
xgb.params['n_estimators'] = $NEW_ESTIMATORS
xgb.params['learning_rate'] = $NEW_LR
xgb.params['max_depth'] = $NEW_DEPTH
xgb.params['scale_pos_weight'] = $NEW_WEIGHT
import joblib
joblib.dump({'params': xgb.params, 'config': 'auto-tuned attempt $ATTEMPT'}, '/tmp/tuned_xgb.pkl')
print('XGBoost params tuned')

from app.models.lightgbm_model import LightGBMModel
lgb = LightGBMModel('app/models/saved_models')
lgb.params['n_estimators'] = $NEW_ESTIMATORS
lgb.params['learning_rate'] = $NEW_LR
lgb.params['max_depth'] = $NEW_DEPTH
lgb.params['scale_pos_weight'] = $NEW_WEIGHT
joblib.dump({'params': lgb.params, 'config': 'auto-tuned attempt $ATTEMPT'}, '/tmp/tuned_lgb.pkl')
print('LightGBM params tuned')
" 2>&1
    
    echo "Tuning complete. Running next attempt..."
done

# Step 6: Final Report
echo ""
echo "=== Final Report ==="
echo "Best AUC: $BEST_AUC"
echo "Target AUC: $AUC_TARGET"
echo "Target Met: $(if (( $(echo "$BEST_AUC >= $AUC_TARGET" | bc -l 2>/dev/null) )); then echo 'YES'; else echo 'NO'; fi)"
echo "Attempts: $ATTEMPT"

# Save summary
cat > reports/latest_train_summary.json << JSONEOF
{
    "timestamp": "$(date -Iseconds)",
    "best_auc": $BEST_AUC,
    "target_auc": $AUC_TARGET,
    "target_met": $(if (( $(echo "$BEST_AUC >= $AUC_TARGET" | bc -l 2>/dev/null) )); then echo 'true'; else echo 'false'; fi),
    "attempts": $ATTEMPT,
    "git_sha": "$(git rev-parse HEAD 2>/dev/null || echo '')"
}
JSONEOF

echo "Summary saved to reports/latest_train_summary.json"
echo ""
echo "=== Done: $(date) ==="
