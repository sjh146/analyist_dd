#!/bin/bash
# auto_pipeline_ci.sh — Auto CI/CD loop for full_pipeline_dd.sh
# Runs repeatedly until all phases pass, max 5 attempts
set +e
cd "$(dirname "$0")/.."
LOG="reports/pipeline_ci_$(date +%Y%m%d_%H%M).log"
MAX_ATTEMPTS=5

echo "=== Auto Pipeline CI Started ===" | tee -a "$LOG"

for attempt in $(seq 1 $MAX_ATTEMPTS); do
    echo "" | tee -a "$LOG"
    echo "=== Attempt $attempt/$MAX_ATTEMPTS ===" | tee -a "$LOG"
    echo "Started: $(date)" | tee -a "$LOG"

    # 1. Cleanup
    bash scripts/cleanup_zombies.sh >> "$LOG" 2>&1

    # 2. Git pull latest
    git pull origin master >> "$LOG" 2>&1

    # 3. Run pipeline (30분 타임아웃)
    timeout 1800 bash scripts/full_pipeline_dd.sh >> "$LOG" 2>&1
    EXIT_CODE=$?

    echo "Exit code: $EXIT_CODE" | tee -a "$LOG"

    if [ $EXIT_CODE -eq 0 ]; then
        echo "✅ PIPELINE PASSED on attempt $attempt!" | tee -a "$LOG"
        git add -A && git commit -m "ci: pipeline PASS attempt $attempt" && git push >> "$LOG" 2>&1
        exit 0
    fi

    # Check which phase was reached
    LAST_PHASE=$(grep -o "Phase [0-9]" "$LOG" 2>/dev/null | tail -1)
    echo "Reached: $LAST_PHASE" | tee -a "$LOG"

    # Commit and retry
    git add -A && git commit -m "ci: pipeline FAIL attempt $attempt ($LAST_PHASE)" >> "$LOG" 2>&1
    git push >> "$LOG" 2>&1

    if [ $attempt -lt $MAX_ATTEMPTS ]; then
        echo "Waiting 60s before retry..." | tee -a "$LOG"
        sleep 60
    fi
done

echo "❌ All $MAX_ATTEMPTS attempts failed" | tee -a "$LOG"
exit 1
