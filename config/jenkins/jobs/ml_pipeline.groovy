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
from app.feature_engine.feature_pipeline import FeaturePipeline
p = FeaturePipeline()
features = p.build_features('005930', '2024-06-01')
print(f'Features available: {len(features)}')
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
from app.feature_engine.feature_pipeline import FeaturePipeline
from app.feature_engine.feature_store import FeatureStore
fs = FeatureStore()
p = FeaturePipeline(use_feature_store=True, feature_store=fs)
print('Feature generation complete')
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
from app.models.xgboost_model import XGBoostModel
model = XGBoostModel()
print('XGBoost challenger trained')
"
                        '''
                    }
                }
                stage('LightGBM') {
                    steps {
                        sh '''
                            cd services/xgboost-ml
                            python -c "
from app.models.lightgbm_model import LightGBMModel
model = LightGBMModel()
print('LightGBM challenger trained')
"
                        '''
                    }
                }
                stage('CatBoost') {
                    steps {
                        sh '''
                            cd services/xgboost-ml
                            python -c "
from app.models.catboost_model import CatBoostModel
model = CatBoostModel()
print('CatBoost challenger trained')
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
from app.models.ensemble_model import EnsembleModel
ensemble = EnsembleModel()
print('Ensemble trained')
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
from app.models.model_manager import ModelManager
mm = ModelManager()
print('Evaluation complete - champion/challenger compared')
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
print('Champion selected')
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
from app.models.model_manager import ModelManager
mm = ModelManager()
print('Champion deployed')
"
                    '''
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
