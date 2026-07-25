pipeline {
    agent any
    triggers { cron('H/30 * * * 1-5') }
    environment { CONTAINER = 'stock_xgboost_ml' }
    stages {
        stage('Train') {
            steps {
                script {
                    sh 'docker exec ${CONTAINER} python -u /tmp/train_v4.py 2>&1 | tee /tmp/jenkins_train.log'
                    def auc = sh(script: "docker exec ${CONTAINER} python3 -c \"import json; d=json.load(open('/app/app/models/saved_models/training-result-v4.json')); print(d.get('auc',0))\"", returnStdout: true).trim()
                    echo "AUC=${auc}"
                    if (auc.toDouble() >= 0.65) { sh "docker exec ${CONTAINER} touch /tmp/auc_met" }
                }
            }
        }
        stage('Commit') {
            steps { sh 'cd /home/dduckbeagy/analyist_dd && git add -A && git diff --cached --quiet || git commit -m "auto: loop" && git push origin master 2>&1 | tail -2' }
        }
    }
}
