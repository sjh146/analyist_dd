#!/bin/bash
set +e
cd "$(dirname "$0")/.."
LOG_DIR="reports"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/pipeline_ci_$TIMESTAMP.log"
RUN_LOG="$LOG_DIR/pipeline_ci_runs.log"

exec > >(tee -a "$LOG_FILE") 2>&1

MAX_ATTEMPTS=5
ATTEMPT=1
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")

echo "============================================================"
echo "  analyist_dd AUTO CI/CD PIPELINE"
echo "  Started: $(date)"
echo "  Branch: $GIT_BRANCH"
echo "  Max attempts: $MAX_ATTEMPTS"
echo "============================================================"

phase_reached() {
    local phase_file="/tmp/pipeline_phase_progress.txt"
    if [ -f "$phase_file" ]; then cat "$phase_file"; else echo "0"; fi
}

phase_name() {
    case "$1" in
        0) echo "Start" ;; 1) echo "1-1 yfinance OHLCV" ;; 2) echo "1-2 KRX Trading" ;;
        3) echo "1-3 News" ;; 4) echo "1-4 Econ Calendar" ;; 5) echo "1-5 Financials" ;;
        6) echo "1-6 Vectorizer" ;; 7) echo "2 ML Training" ;; 8) echo "3 Swing Analysis" ;;
        9) echo "4 Backtest" ;; 10) echo "5 Strategies" ;; 11) echo "6+7 Cleanup/Report" ;;
        *) echo "Unknown" ;;
    esac
}

while [ $ATTEMPT -le $MAX_ATTEMPTS ]; do
    echo ""
    echo "############################################################"
    echo "  ATTEMPT $ATTEMPT / $MAX_ATTEMPTS"
    echo "  Started: $(date)"
    echo "############################################################"

    LAST_PHASE=$(phase_reached)
    echo "Last completed phase: $(phase_name $LAST_PHASE)"

    if [ $ATTEMPT -gt 1 ]; then
        echo "Waiting 30s for retry..."
        sleep 30
        docker compose ps --format 'table {{.Names}}\t{{.Status}}'
    fi

    bash scripts/full_pipeline_dd.sh
    PIPELINE_EXIT=$?

    echo ""
    echo "Pipeline exit code: $PIPELINE_EXIT"

    if [ $PIPELINE_EXIT -eq 0 ]; then
        echo ""
        echo "============================================================"
        echo "  PIPELINE SUCCEEDED on attempt $ATTEMPT"
        echo "============================================================"
        echo "$(date +%Y-%m-%d_%H:%M:%S) | ATT $ATTEMPT | SUCCESS | Exit=0" >> "$RUN_LOG"
        git add reports/ -A 2>/dev/null || true
        git add -A 2>/dev/null || true
        git commit -m "auto-ci: pipeline PASS at attempt $ATTEMPT ($(date +%Y%m%d_%H%M))" 2>/dev/null || true
        exit 0
    fi

    echo ""
    echo "--- ATTEMPT $ATTEMPT FAILED ---"
    LAST_PHASE=$(phase_reached)
    FAILED_AT=$(phase_name $((LAST_PHASE + 1)))
    echo "Failed at: $FAILED_AT"
    echo "Last OK: $(phase_name $LAST_PHASE)"

    echo "$(date +%Y-%m-%d_%H:%M:%S) | ATT $ATTEMPT | FAIL | Phase=$FAILED_AT | Exit=$PIPELINE_EXIT" >> "$RUN_LOG"

    git add reports/ -A 2>/dev/null || true
    git add -A 2>/dev/null || true
    git commit -m "auto-ci: pipeline FAIL at attempt $ATTEMPT ($FAILED_AT) $(date +%Y%m%d_%H%M)" 2>/dev/null || true

    ATTEMPT=$((ATTEMPT + 1))
    if [ $ATTEMPT -le $MAX_ATTEMPTS ]; then
        echo ""
        echo "--- Next attempt $ATTEMPT/$MAX_ATTEMPTS in 60s ---"
        sleep 60
    fi
done

echo ""
echo "============================================================"
echo "  MAX ATTEMPTS ($MAX_ATTEMPTS) REACHED"
echo "  Pipeline did not complete. Check: $LOG_FILE"
echo "============================================================"
exit 1
