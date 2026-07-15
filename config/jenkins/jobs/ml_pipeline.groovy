pipeline {
    agent any
    
    triggers {
        cron('0 20 * * 1-5')
    }
    
    environment {
        PYTHONPATH = "${WORKSPACE}"
        MODEL_PATH = "${WORKSPACE}/models/saved_models"
        FEATURE_STORE = "true"
    }
    
    stages {
        stage('Data Collection Check') {
            steps {
                script {
                    sh '''
                        cd services/xgboost-ml
                        python -c "
import json, sys
from app.training.auto_retrain import AutoRetrainer
retrainer = AutoRetrainer()
result = retrainer.check_data_collection()
print(json.dumps(result))
if result['status'] == 'error':
    sys.exit(1)
"
                    '''
                }
            }
        }
        
        stage('Feature Generation') {
            steps {
                script {
                    sh '''
                        cd services/xgboost-ml
                        python -c "
import json, sys
from app.training.auto_retrain import AutoRetrainer
retrainer = AutoRetrainer()
result = retrainer.generate_features(days=365)
print(json.dumps(result))
if result['status'] == 'error':
    sys.exit(1)
"
                    '''
                }
            }
        }
        
        stage('Model Training (Challenger)') {
            parallel {
                stage('XGBoost') {
                    steps {
                        sh '''
                            cd services/xgboost-ml
                            python -c "
import json, sys
from app.training.auto_retrain import AutoRetrainer
retrainer = AutoRetrainer()
data_result = retrainer.prepare_data(days=365)
if data_result['status'] == 'error':
    print(json.dumps(data_result))
    sys.exit(1)
result = retrainer.train_challengers()
print(json.dumps(result))
if result['models'].get('xgboost', {}).get('status') != 'trained':
    sys.exit(1)
"
                        '''
                    }
                }
                stage('LightGBM') {
                    steps {
                        sh '''
                            cd services/xgboost-ml
                            python -c "
import json, sys
from app.training.auto_retrain import AutoRetrainer
retrainer = AutoRetrainer()
data_result = retrainer.prepare_data(days=365)
if data_result['status'] == 'error':
    print(json.dumps(data_result))
    sys.exit(1)
result = retrainer.train_challengers()
print(json.dumps(result))
if result['models'].get('lightgbm', {}).get('status') != 'trained':
    sys.exit(1)
"
                        '''
                    }
                }
                stage('CatBoost') {
                    steps {
                        sh '''
                            cd services/xgboost-ml
                            python -c "
import json, sys
from app.training.auto_retrain import AutoRetrainer
retrainer = AutoRetrainer()
data_result = retrainer.prepare_data(days=365)
if data_result['status'] == 'error':
    print(json.dumps(data_result))
    sys.exit(1)
result = retrainer.train_challengers()
print(json.dumps(result))
if result['models'].get('catboost', {}).get('status') != 'trained':
    sys.exit(1)
"
                        '''
                    }
                }
            }
        }
        
        stage('Ensemble Training') {
            steps {
                script {
                    sh '''
                        cd services/xgboost-ml
                        python -c "
import json, sys
from app.training.auto_retrain import AutoRetrainer
retrainer = AutoRetrainer()
result = retrainer.train_ensemble()
print(json.dumps(result))
if result['status'] == 'error':
    sys.exit(1)
"
                    '''
                }
            }
        }
        
        stage('Evaluation') {
            steps {
                script {
                    sh '''
                        cd services/xgboost-ml
                        python -c "
import json, sys, os
from app.training.auto_retrain import AutoRetrainer, CHAMPION_DIR, CHALLENGER_DIR
retrainer = AutoRetrainer()
# Find champion and challenger model files
champion_pkl = None
challenger_pkl = None
if os.path.isdir(CHAMPION_DIR):
    files = sorted([f for f in os.listdir(CHAMPION_DIR) if f.endswith('.pkl')])
    if files:
        champion_pkl = os.path.join(CHAMPION_DIR, files[0])
if os.path.isdir(CHALLENGER_DIR):
    files = sorted([f for f in os.listdir(CHALLENGER_DIR) if f.endswith('.pkl') and f != 'ensemble_model.pkl'])
    if files:
        challenger_pkl = os.path.join(CHALLENGER_DIR, files[0])
if not champion_pkl or not challenger_pkl:
    print(json.dumps({'status': 'error', 'message': 'Missing champion or challenger model'}))
    sys.exit(1)
result = retrainer.evaluate(champion_pkl, challenger_pkl)
print(json.dumps(result))
if result['status'] == 'error':
    sys.exit(1)
"
                    '''
                }
            }
        }
        
        stage('Champion Selection') {
            steps {
                script {
                    sh '''
                        cd services/xgboost-ml
                        python -c "
import json, sys
from app.training.auto_retrain import AutoRetrainer
retrainer = AutoRetrainer()
with open('ml_metrics/eval_results.json', 'r') as f:
    eval_results = json.load(f)
selection = retrainer.select_champion(eval_results)
print(json.dumps(selection))
# Write selection result for Deploy stage
with open('ml_metrics/champion_selection.json', 'w') as f:
    json.dump(selection, f)
"
                    '''
                }
            }
        }
        
        stage('Drift Detection') {
            steps {
                script {
                    sh '''
                        cd services/xgboost-ml && python -c "
from app.monitoring.drift_detector import DriftDetector
from app.monitoring.alerter import Alerter
import json, os
dd = DriftDetector()
alerter = Alerter()
# Performance drift
baseline = dd.get_baseline_metrics(os.environ.get('ML_MODEL_VERSION', 'v1.0'))
with open('ml_metrics/champion_metrics.json') as f: current = json.load(f)
perf = dd.check_performance_drift(current, baseline)
if perf['drift_detected']: dd.store_drift_result('ALL', 'performance', perf); alerter.send_drift_alert(perf)
# Data drift check
print('Drift detection complete')
"
                    '''
                }
            }
        }
        
        stage('Deploy') {
            steps {
                script {
                    sh '''
                        cd services/xgboost-ml
                        python -c "
import json, sys
from app.training.auto_retrain import AutoRetrainer
retrainer = AutoRetrainer()
with open('ml_metrics/champion_selection.json', 'r') as f:
    selection = json.load(f)
result = retrainer.deploy(selection)
print(json.dumps(result))
if not result.get('deployed', False):
    print('Deploy skipped: challenger did not outperform champion')
"
                        '''
                    }
                }
            }
        }
    }
            }
        }
    }
    
    post {
        success {
            echo 'ML Pipeline completed successfully'
        }
        failure {
            echo 'ML Pipeline failed'
        }
    }
}
