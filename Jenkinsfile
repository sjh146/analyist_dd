pipeline {
    agent any
    
    triggers {
        cron('0 20 * * 1-5')
    }
    
    stages {
        stage('Pipeline') {
            steps {
                script {
                    load 'config/jenkins/jobs/ml_pipeline.groovy'
                }
            }
        }
    }
}
