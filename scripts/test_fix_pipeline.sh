#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/reports"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/test_fix_pipeline_$TIMESTAMP.log"
START_TIME=$(date +%s)

TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-1800}"
MAX_RETRIES="${MAX_RETRIES:-3}"

mkdir -p "$LOG_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

log() {
    local level="$1"
    shift
    echo "[$(date '+%H:%M:%S')] [$level] $*"
}

print_summary() {
    local status="$1"
    local attempts="$2"
    local end_time
    end_time="$(date +%s)"
    local elapsed=$((end_time - START_TIME))
    local mins=$((elapsed / 60))
    local secs=$((elapsed % 60))

    echo ""
    echo "========================================"
    echo "  TEST-FIX PIPELINE SUMMARY"
    echo "========================================"
    echo "  Final status: $status"
    echo "  Attempts:     $attempts / $MAX_RETRIES"
    echo "  Total time:   ${mins}m ${secs}s"
    echo "  Log file:     $LOG_FILE"
    echo "========================================"
    echo ""
}

cleanup() {
    local exit_code=$?
    log "WARN" "Interrupted by user (SIGINT/SIGTERM)"
    print_summary "INTERRUPTED" "$attempt"
    exit "$exit_code"
}
trap cleanup SIGINT SIGTERM

main() {
    log "INFO" "=== Test-Fix Pipeline Started ==="
    log "INFO" "Timeout: ${TIMEOUT_SECONDS}s, Max retries: $MAX_RETRIES"
    echo ""

    local attempt=1
    local success=false
    local final_status="FAIL"
    local latest_log=""

    while [[ $attempt -le $MAX_RETRIES && "$success" != "true" ]]; do
        log "INFO" "=== Attempt $attempt / $MAX_RETRIES ==="

        log "STEP" "Step 1: Pulling latest code..."
        git -C "$PROJECT_DIR" pull --rebase origin main 2>&1 || log "WARN" "Git pull failed, continuing..."

        log "STEP" "Step 2: Verifying Docker services..."
        if ! docker compose ps 2>&1; then
            log "WARN" "docker compose ps failed, attempting restart..."
            docker compose up -d 2>&1 || true
            sleep 10
        fi

        log "STEP" "Step 3: Cleaning up zombie processes..."
        bash "$PROJECT_DIR/scripts/cleanup_zombies.sh" 2>&1 || true

        log "STEP" "Step 4: Running full_pipeline_dd.sh (timeout: ${TIMEOUT_SECONDS}s)..."
        set +e
        timeout "$TIMEOUT_SECONDS" bash "$PROJECT_DIR/scripts/full_pipeline_dd.sh" 2>&1
        local exit_code=$?
        set -e

        log "INFO" "full_pipeline_dd.sh exited with code $exit_code"

        if [[ $exit_code -eq 0 ]]; then
            log "PASS" "Pipeline attempt $attempt SUCCEEDED"
            success=true
            final_status="PASS"
        else
            log "FAIL" "Pipeline attempt $attempt FAILED (exit=$exit_code)"
            log "INFO" "Checking error log..."
            latest_log=$(ls -t "$LOG_DIR"/full_pipeline_dd_*.log 2>/dev/null | head -1 || echo "")
            if [[ -n "$latest_log" ]]; then
                log "INFO" "Error log: $latest_log"
                tail -30 "$latest_log"
                cp "$latest_log" "$LOG_DIR/test_fix_attempt_${attempt}_failed.log"
            fi

            if [[ $attempt -lt $MAX_RETRIES ]]; then
                log "STEP" "Committing changes and waiting before retry..."
                git -C "$PROJECT_DIR" add -A 2>&1 || true
                git -C "$PROJECT_DIR" diff --cached --quiet || \
                    git -C "$PROJECT_DIR" commit -m "auto: test-fix attempt $attempt failed, retrying" 2>&1 || true
                git -C "$PROJECT_DIR" push origin main 2>&1 || log "WARN" "Git push failed"

                log "INFO" "Waiting 60 seconds before retry..."
                sleep 60
            fi
        fi

        attempt=$((attempt + 1))
        echo ""
    done

    if [[ "$success" != "true" ]]; then
        log "FAIL" "MAX RETRIES REACHED ($MAX_RETRIES). Pipeline did not succeed."
        final_status="FAIL"
        if [[ -n "$latest_log" ]]; then
            log "INFO" "Last error log: $latest_log"
            tail -50 "$latest_log"
        fi
    fi

    print_summary "$final_status" $((attempt - 1))

    if [[ "$final_status" == "PASS" ]]; then
        exit 0
    else
        exit 1
    fi
}

main "$@"
