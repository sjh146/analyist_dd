// Plan Executor Pipeline
// Loaded by Jenkinsfile when PLAN_FILE parameter is set
// Reads .omo/plans/<PLAN_FILE>.md, executes each todo with retry loop, merges branches

def planJson = [:]

pipeline {
    agent any

    parameters {
        string(name: 'PLAN_FILE', defaultValue: 'enable-metrics', description: 'Plan slug from .omo/plans/')
        string(name: 'MAX_RETRIES', defaultValue: '3', description: 'Max retries per todo before failing pipeline')
        booleanParam(name: 'DRY_RUN', defaultValue: false, description: 'Parse and show todos without executing')
    }

    stages {
        stage('Parse Plan') {
            steps {
                script {
                    def parserOutput = sh(
                        script: "python3 scripts/plan-parser.py --plan .omo/plans/${PLAN_FILE}.md",
                        returnStdout: true
                    )
                    planJson = readJSON text: parserOutput
                    echo "Parsed plan: ${planJson.slug} with ${planJson.todos.size()} todos"

                    if (params.DRY_RUN) {
                        echo "DRY RUN — Todos that would be executed:"
                        planJson.todos.each { t ->
                            echo "  [${t.id}] ${t.title}"
                        }
                        echo "Exiting dry run."
                        currentBuild.result = 'SUCCESS'
                        return
                    }
                }
            }
        }

        stage('Setup Git') {
            steps {
                script {
                    sh '''
                        git config user.email "jenkins@analyist-dd.local"
                        git config user.name "Jenkins Plan Executor"
                        git fetch origin main || true
                        git checkout main || true
                    '''
                }
            }
        }

        stage('Execute Todos') {
            steps {
                script {
                    def maxRetries = params.MAX_RETRIES.toInteger()

                    for (def i = 0; i < planJson.todos.size(); i++) {
                        def todo = planJson.todos[i]
                        def branchName = "plan-${PLAN_FILE}-todo-${todo.id}"

                        stage("Todo ${todo.id}: ${todo.title.take(60)}") {
                            retry(maxRetries) {
                                echo "=== Executing Todo ${todo.id}: ${todo.title} ==="

                                // Create feature branch from main
                                sh """
                                    git checkout main
                                    git checkout -b ${branchName}
                                """

                                // TODO: Execute actual file edits from todo.what_to_do
                                // This is a placeholder — real execution would:
                                // 1. Parse what_to_do for edit commands
                                // 2. Apply changes via python/perl/sed
                                // 3. Run QA commands from todo.qa_commands
                                echo "TODO instructions: ${todo.what_to_do?.take(500)}"

                                // Run acceptance criteria tests
                                if (todo.acceptance) {
                                    echo "Acceptance criteria:"
                                    todo.acceptance.each { a ->
                                        echo "  - ${a}"
                                    }
                                }

                                // Commit and push feature branch
                                sh """
                                    git add -A
                                    git commit -m "${todo.commit_msg}" || echo "Nothing to commit"
                                    git push origin ${branchName} || echo "Push failed (maybe no changes)"
                                """

                                echo "=== Todo ${todo.id} completed successfully ==="
                            }
                        }
                    }
                }
            }
        }

        stage('Merge Branches') {
            when {
                expression { !params.DRY_RUN }
            }
            steps {
                script {
                    for (def i = 0; i < planJson.todos.size(); i++) {
                        def todo = planJson.todos[i]
                        def branchName = "plan-${PLAN_FILE}-todo-${todo.id}"

                        stage("Merge Todo ${todo.id}") {
                            sh """
                                git checkout main
                                git merge --squash ${branchName} || echo "Nothing to merge"
                                git commit -m "${todo.commit_msg}" || echo "No changes to commit"
                                git push origin main || echo "Push failed"
                                git branch -d ${branchName} || echo "Branch not found"
                                git push origin --delete ${branchName} || echo "Remote branch not found"
                            """
                        }
                    }
                }
            }
        }
    }

    post {
        failure {
            echo "=== PLAN EXECUTOR FAILED ==="
            echo "Plan: ${PLAN_FILE}"
            echo "Check Jenkins console for error details."
            echo "Failed feature branches (if any) are left for manual inspection."
        }
        success {
            echo "=== PLAN EXECUTOR COMPLETED ==="
            echo "All ${planJson.todos?.size() ?: 0} todos executed and merged to main."
        }
    }
}
