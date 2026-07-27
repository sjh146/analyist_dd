pipeline {
    agent any
    triggers { cron('H/30 * * * *') }
    environment { CONTAINER = 'stock_xgboost_ml' }
    stages {
        stage('Infinite Loop') {
            steps {
                script {
                    sh 'bash /home/dduckbeagy/analyist_dd/scripts/ml_infinite_loop.sh 2>&1'
                    def auc = sh(script: "docker exec ${CONTAINER} python3 -c \"import json; import glob; best=0; f=''; for fp in glob.glob('/app/app/models/saved_models/training-result-v*.json'): d=json.load(open(fp)); a=d.get('auc',0); print(a)\" 2>/dev/null | sort -rn | head -1", returnStdout: true).trim()
                    echo "Best AUC: ${auc}"
                    if (auc.toDouble() >= 0.65) { sh "docker exec ${CONTAINER} touch /tmp/auc_met" }
                }
            }
        }
    }
}
