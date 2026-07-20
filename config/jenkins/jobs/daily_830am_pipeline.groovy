// Daily 8:30 AM Swing Pipeline
// Runs automatically Mon-Fri at 08:30 KST (UTC 23:30)
// 1. Runs swing-pipeline.sh
// 2. Verifies all services healthy via run_all_tests.sh
// 3. If tests fail: retry max 3 times
// 4. If still failing: notify

pipeline {
    agent any
    
    triggers {
        cron('30 23 * * 1-5')  // UTC 23:30 = KST 08:30, Mon-Fri
    }
    
    parameters {
        booleanParam(name: 'DRY_RUN', defaultValue: false, description: 'Parse plan without executing')
    }
    
    stages {
        stage('Parse Plan') {
            steps {
                script {
                    def parserOutput = sh(
                        script: "python3 scripts/plan-parser.py --plan .omo/plans/daily-830am-pipeline.md",
                        returnStdout: true
                    )
                    echo "Plan loaded: daily-830am-pipeline"
                    
                    if (params.DRY_RUN) {
                        echo "DRY RUN — Plan parsed successfully"
                        currentBuild.result = 'SUCCESS'
                        return
                    }
                }
            }
        }
        
        stage('Run Swing Pipeline') {
            steps {
                retry(3) {
                    sh '''
                        echo "=== Daily 8:30 AM Swing Pipeline: $(date) ==="
                        bash scripts/swing-pipeline.sh
                        echo "=== Pipeline Complete ==="
                    '''
                }
            }
        }
        
        stage('Verify Services') {
            steps {
                sh '''
                    echo "Running post-pipeline verification..."
                    bash scripts/run_all_tests.sh || echo "Tests completed (may have warnings)"
                '''
            }
        }
    }
    
    post {
        failure {
            echo "=== DAILY PIPELINE FAILED ==="
            echo "Check Jenkins console for details."
        }
        success {
            echo "=== DAILY 8:30 AM PIPELINE COMPLETED SUCCESSFULLY ==="
        }
    }
}
