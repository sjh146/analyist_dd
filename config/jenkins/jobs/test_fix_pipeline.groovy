// Test-Fix Pipeline
// Runs every 2 hours (or manual trigger)
// 1. git pull latest code
// 2. Verify all 16 Docker services are "Up"
// 3. Cleanup zombie processes
// 4. Run full_pipeline_dd.sh with 30-min timeout
// 5. If fails: parse error, commit fix, retry (max 3)
// 6. Archive logs

TIMEOUT_SECONDS = 1800
MAX_RETRIES = 3

pipeline {
    agent any

    triggers {
        cron('H */2 * * *')
    }

    parameters {
        booleanParam(name: 'DRY_RUN', defaultValue: false, description: 'Parse steps without executing pipeline')
        booleanParam(name: 'SKIP_CLEANUP', defaultValue: false, description: 'Skip zombie cleanup step')
    }

    stages {
        stage('1. Pull Latest Code') {
            steps {
                script {
                    echo "=== $(date) Pulling latest code ==="
                    sh 'git pull --rebase origin main || echo "Already up to date"'
                }
            }
        }

        stage('2. Verify Docker Services') {
            steps {
                script {
                    echo "=== Verifying all Docker services are Up ==="
                    def output = sh(script: 'docker compose ps --format "{{.Names}}\t{{.Status}}"', returnStdout: true).trim()
                    echo output
                    def lines = output.split('\n')
                    int total = lines.length
                    int upCount = 0
                    for (line in lines) {
                        if (line.contains('Up')) upCount++
                    }
                    if (params.DRY_RUN) {
                        echo "DRY RUN — Would verify $total services, $upCount Up"
                        return
                    }
                    if (upCount < total) {
                        error "Not all services are Up ($upCount/$total). Aborting."
                    }
                    echo "All $upCount/$total services are Up."
                }
            }
        }

        stage('3. Cleanup Zombie Processes') {
            when { expression { !params.SKIP_CLEANUP } }
            steps {
                script {
                    echo "=== Running cleanup_zombies.sh ==="
                    sh 'bash scripts/cleanup_zombies.sh'
                }
            }
        }

        stage('4. Test-Fix Loop') {
            steps {
                script {
                    def attempt = 1
                    def success = false
                    def logFile = ''

                    while (attempt <= MAX_RETRIES && !success) {
                        echo "=== Test-Fix attempt ${attempt}/${MAX_RETRIES} ==="

                        if (params.DRY_RUN) {
                            echo "DRY RUN — Would execute: timeout $TIMEOUT_SECONDS bash scripts/full_pipeline_dd.sh"
                            attempt++
                            continue
                        }

                        // Capture latest log file before running
                        def latestBefore = sh(
                            script: 'ls -t reports/full_pipeline_dd_*.log 2>/dev/null | head -1 || echo ""',
                            returnStdout: true
                        ).trim()

                        def exitCode = sh(
                            script: "timeout $TIMEOUT_SECONDS bash scripts/full_pipeline_dd.sh",
                            returnStatus: true
                        )

                        // Find the latest log file after run
                        logFile = sh(
                            script: 'ls -t reports/full_pipeline_dd_*.log 2>/dev/null | head -1 || echo ""',
                            returnStdout: true
                        ).trim()

                        if (exitCode == 0) {
                            echo "=== Pipeline attempt ${attempt} SUCCEEDED (exit=0) ==="
                            success = true
                        } else {
                            echo "=== Pipeline attempt ${attempt} FAILED (exit=${exitCode}) ==="
                            if (logFile && logFile != latestBefore) {
                                echo "=== Error log: ${logFile} ==="
                                sh "tail -30 ${logFile}"
                                sh "cp ${logFile} reports/test_fix_attempt_${attempt}_failed.log"
                            }

                            if (attempt < MAX_RETRIES) {
                                echo "=== Committing partial fix attempt ${attempt} and retrying ==="
                                sh """
                                    git add -A
                                    git diff --cached --quiet || git commit -m 'auto: test-fix attempt ${attempt} failed, retrying'
                                    git push origin main 2>&1 || echo 'Push skipped (no changes or remote error)'
                                """
                            }
                        }

                        attempt++
                    }

                    if (!success) {
                        echo "=== TEST-FIX LOOP EXHAUSTED after ${MAX_RETRIES} attempts ==="
                        echo "=== Last log: ${logFile} ==="
                        if (logFile) {
                            sh "tail -50 ${logFile}"
                        }
                        error "Test-fix pipeline failed after ${MAX_RETRIES} retries."
                    }
                }
            }
        }

        stage('5. Archive Logs') {
            steps {
                script {
                    echo "=== Archiving pipeline logs ==="
                    sh 'mkdir -p reports/archive'
                    sh 'cp reports/full_pipeline_dd_*.log reports/archive/ 2>/dev/null || true'
                    sh 'cp reports/test_fix_pipeline_*.log reports/archive/ 2>/dev/null || true'
                }
            }
        }
    }

    post {
        success {
            echo "=== TEST-FIX PIPELINE COMPLETED SUCCESSFULLY ==="
        }
        failure {
            echo "=== TEST-FIX PIPELINE FAILED ==="
            echo "Check Jenkins console for details."
        }
        unstable {
            echo "=== TEST-FIX PIPELINE UNSTABLE ==="
        }
        always {
            archiveArtifacts artifacts: 'reports/full_pipeline_dd_*.log, reports/test_fix_pipeline_*.log, reports/backtest_result.json, reports/swing_candidates.json, reports/ml_result.json', allowEmptyArchive: true
        }
    }
}
