pipeline {
    agent any

    parameters {
        string(name: 'PLAN_FILE', defaultValue: '', description: 'Plan slug from .omo/plans/ (leave empty for ML pipeline, "daily" for daily 8:30AM pipeline)')
    }

    triggers {
        cron('0 20 * * 1-5')
    }

    stages {
        stage('Pipeline') {
            steps {
                script {
                    if (env.PLAN_FILE == 'daily-830am-pipeline') {
                        load 'config/jenkins/jobs/daily_830am_pipeline.groovy'
                    } else if (env.PLAN_FILE) {
                        load 'config/jenkins/jobs/plan-executor.groovy'
                    } else {
                        load 'config/jenkins/jobs/ml_pipeline.groovy'
                    }
                }
            }
        }
    }
}
