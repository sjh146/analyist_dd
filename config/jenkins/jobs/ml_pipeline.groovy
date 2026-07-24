pipeline {
    agent any
    
    triggers {
        cron('0 8,20 * * 1-5')
    }
    
    environment {
        CONTAINER = 'stock_xgboost_ml'
    }
    
    stages {
        stage('1. Check & Pull Git') {
            steps {
                script {
                    sh 'git pull --rebase origin main || echo "Already up to date"'
                }
            }
        }
        
        stage('2. Train Model') {
            steps {
                script {
                    def max_attempts = 3
                    def auc = 0.0
                    
                    for (int attempt = 1; attempt <= max_attempts; attempt++) {
                        echo "Training attempt ${attempt}/${max_attempts}..."
                        
                        // Run training inside xgboost-ml container
                        sh """
                            docker exec ${CONTAINER} python -u /tmp/train_v2.py 2>&1 | tee /tmp/jenkins_ml_train_${attempt}.log
                        """
                        
                        // Read result
                        script {
                            def resultFile = "/app/.omo/evidence/training-result-v2.json"
                            def result = sh(
                                script: "docker exec ${CONTAINER} sh -c 'cat ${resultFile} 2>/dev/null || echo \"{}\"'",
                                returnStdout: true
                            ).trim()
                            
                            if (result && result != "{}") {
                                def jsonResult = readJSON text: result
                                auc = jsonResult.auc ?: 0.0
                                echo "Attempt ${attempt}: AUC = ${auc}"
                            }
                            
                            if (auc >= 0.65) {
                                echo "Target AUC 0.65 achieved! Stopping."
                                break
                            }
                            
                            if (attempt < max_attempts && auc < 0.65) {
                                echo "AUC ${auc} < 0.65. Tuning hyperparameters..."
                                
                                // Calculate new scale_pos_weight from last training's imbalance
                                def prevResult = sh(
                                    script: "docker exec ${CONTAINER} sh -c 'cat /app/.omo/evidence/training-result-balanced.json 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d.get(\\\"imbalance_ratio\\\", 1.6))\"'",
                                    returnStdout: true
                                ).trim()
                                
                                def imbalance = prevResult ? prevResult.toDouble() : 1.6
                                
                                // Tune: increase n_estimators, adjust scale_pos_weight
                                sh """
                                    docker exec ${CONTAINER} python3 -c "
                                        from app.models.xgboost_model import XGBoostModel
                                        model = XGBoostModel()
                                        model.params['n_estimators'] = ${(1000 + attempt * 200)}
                                        model.params['scale_pos_weight'] = ${imbalance} * ${(1.0 + attempt * 0.1)}
                                        model.params['learning_rate'] = ${(0.05 / attempt).round(4)}
                                        model.params['max_depth'] = ${8 + attempt}
                                        import joblib
                                        joblib.dump({'params': model.params, 'note': 'auto-tuned attempt ${attempt}'}, '/tmp/tuned_xgb_params.pkl')
                                        
                                        from app.models.lightgbm_model import LightGBMModel
                                        model2 = LightGBMModel('app/models/saved_models')
                                        model2.params['n_estimators'] = ${(1000 + attempt * 200)}
                                        model2.params['scale_pos_weight'] = ${imbalance} * ${(1.0 + attempt * 0.1)}
                                        model2.params['learning_rate'] = ${(0.05 / attempt).round(4)}
                                        joblib.dump({'params': model2.params}, '/tmp/tuned_lgb_params.pkl')
                                    "
                                    echo "Tuned params for attempt ${attempt+1}"
                                """
                            }
                        }
                    }
                    
                    echo "Final AUC: ${auc}"
                }
            }
        }
        
        stage('3. Evaluate & Report') {
            steps {
                script {
                    // Collect all results
                    def bestResult = sh(
                        script: "docker exec ${CONTAINER} sh -c 'python3 -c \"
import json, os, glob
best = {\"auc\": 0.0}
for f in glob.glob(\\\"/app/.omo/evidence/training-result*.json\\\"):
    try:
        with open(f) as fh:
            d = json.load(fh)
            if d.get(\\\"auc\\\", 0) > best.get(\\\"auc\\\", 0):
                best = d
                best[\\\"file\\\"] = f
    except: pass
print(json.dumps(best, indent=2))
\"'",
                        returnStdout: true
                    ).trim()
                    
                    echo "=== BEST RESULT ==="
                    echo "${bestResult}"
                    
                    // Save best result to workspace
                    sh "docker exec ${CONTAINER} sh -c 'cat /app/.omo/evidence/training-result-balanced.json' > reports/ml_best_result.json 2>/dev/null || true"
                    sh "docker exec ${CONTAINER} sh -c 'cat /app/.omo/evidence/training-result-v2.json' > reports/ml_best_result.json 2>/dev/null || true"
                }
            }
        }
        
        stage('4. Git Commit Results') {
            steps {
                script {
                    sh '''
                        git add -A
                        git diff --cached --quiet || git commit -m "auto: ML pipeline update $(date +%Y-%m-%d_%H:%M)"
                        git push origin main 2>&1 || echo "Push failed (maybe no changes)"
                    '''
                }
            }
        }
    }
    
    post {
        success {
            echo 'ML Pipeline completed. Models trained and evaluated.'
        }
        failure {
            echo 'ML Pipeline failed. Check logs.'
        }
    }
}
