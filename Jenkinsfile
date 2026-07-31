pipeline {
    agent any

    parameters {
        string(name: 'PLAN_FILE', defaultValue: '', description: 'Plan slug from .omo/plans/ (leave empty for ML pipeline, "daily" for daily 8:30AM pipeline)')
        booleanParam(name: 'TEST_FIX_PIPELINE', defaultValue: false, description: 'Run the automated test-fix pipeline instead of the standard pipeline')
        booleanParam(name: 'AUTO_CI_PIPELINE', defaultValue: false, description: 'Run the auto CI/CD loop (auto_pipeline_ci.sh)')
    }

    triggers {
        cron('0 20 * * 1-5')
        cron('H/30 * * * 1-5')
    }

    environment {
        PIPELINE_TIMEOUT = '1800'
        MAX_RETRIES = '3'
    }

    stages {
        stage('Pipeline') {
            steps {
                script {
                    if (params.AUTO_CI_PIPELINE) {
                        sh 'bash scripts/auto_pipeline_ci.sh'
                    } else if (params.TEST_FIX_PIPELINE) {
                        load 'config/jenkins/jobs/test_fix_pipeline.groovy'
                    } else if (env.PLAN_FILE == 'infinite-loop') {
                        load 'config/jenkins/jobs/ml_infinite_loop.groovy'
                    } else if (env.PLAN_FILE == 'daily-830am-pipeline') {
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
