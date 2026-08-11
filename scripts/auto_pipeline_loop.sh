#!/bin/bash
# auto_pipeline_loop.sh — Infinite pipeline + test + fix loop
# Runs continuously, never asks for input.

set +e
cd "$(dirname "$0")/.."
LOG_DIR="reports"
mkdir -p "$LOG_DIR"

ITERATION=0
MAX_FIX_ATTEMPTS=5

while true; do
    ITERATION=$((ITERATION + 1))
    TIMESTAMP=$(date +%Y%m%d_%H%M)
    echo ""
    echo "============================================"
    echo "  AUTO PIPELINE LOOP — Iteration $ITERATION"
    echo "  Started: $(date)"
    echo "============================================"

    # Step 1: Run full_pipeline_dd.sh
    echo ">>> [Step 1] Running full_pipeline_dd.sh..."
    bash scripts/full_pipeline_dd.sh 2>&1 | tail -20
    echo "full_pipeline_dd.sh exit code: $?"

    # Step 2: Check test infrastructure and install pytest if needed
    echo ">>> [Step 2] Ensuring test infrastructure..."
    docker exec stock_xgboost_ml pip install pytest pytest-json-report -q 2>/dev/null
    docker exec stock_xgboost_ml python3 -m pytest --version > /dev/null 2>&1
    echo "pytest available: $?"

    # Step 3: Run tests
    echo ">>> [Step 3] Running tests..."
    TEST_OUTPUT=$(docker exec stock_xgboost_ml python3 -m pytest /app/tests/ -v --tb=short 2>&1)
    TEST_EXIT=$?
    echo "$TEST_OUTPUT" | tail -30
    
    PASS_COUNT=$(echo "$TEST_OUTPUT" | grep -c "PASSED")
    FAIL_COUNT=$(echo "$TEST_OUTPUT" | grep -c "FAILED")
    echo "Tests: $PASS_COUNT passed, $FAIL_COUNT failed"

    # Save test results
    echo "$TEST_OUTPUT" > "$LOG_DIR/test_results_$TIMESTAMP.log"
    
    if [ $TEST_EXIT -eq 0 ]; then
        echo ">>> ALL TESTS PASSED ✅"
        
        # Step 4: Commit all changes
        echo ">>> [Step 4] Committing changes..."
        cd /home/dduckbeagy/analyist_dd
        git add reports/ .omo/evidence/ tests/ 2>/dev/null
        git diff --cached --quiet || git commit -m "auto: pipeline iteration $ITERATION — all tests passed"
        
        # Step 5: Wait before next run
        echo ">>> Waiting 1 hour before next pipeline run..."
        sleep 3600
    else
        echo ">>> TESTS FAILED ($FAIL_COUNT failures) ⚠️"
        
        # Check if max attempts reached
        if [ $FAIL_COUNT -gt 0 ] && [ -f "scripts/fix_agent.sh" ]; then
            echo ">>> [Step 4] Attempting auto-fix..."
            # Run existing fix agent
            FIX_ITERATION=$ITERATION bash scripts/fix_agent.sh 2>&1 | tail -10
            FIX_EXIT=$?
            
            # Commit fix attempt
            cd /home/dduckbeagy/analyist_dd
            git add reports/ .omo/evidence/ tests/ 2>/dev/null
            git diff --cached --quiet || git commit -m "auto: fix attempt $ITERATION — $FAIL_COUNT failures remain"
            
            if [ $ITERATION -ge $MAX_FIX_ATTEMPTS ]; then
                echo ">>> MAX FIX ATTEMPTS REACHED ($MAX_FIX_ATTEMPTS). Resetting..."
                ITERATION=0
                # Try emergency revert
                bash scripts/emergency_revert.sh 1 2>/dev/null
            fi
        fi
        
        # Wait before retry (5 minutes)
        echo ">>> Waiting 5 minutes before retry..."
        sleep 300
    fi
done
