#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# test_fix_loop.sh — Main test-fix loop orchestrator
# Runs tests, parses failures, fixes them, repeats until pass.
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EVIDENCE_DIR="$PROJECT_DIR/.omo/evidence"
LOG_FILE="$EVIDENCE_DIR/test-fix-loop.log"
START_TIME=$(date +%s)

# ── Config ──────────────────────────────────────────────────
MAX_ITER="${MAX_ITER:-5}"
DRY_RUN=false
SKIP_FIX=false

# ── Parse args ──────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --skip-fix) SKIP_FIX=true ;;
    --help|-h)
      echo "Usage: $0 [--dry-run] [--skip-fix]"
      echo ""
      echo "  --dry-run   Print what would be done without executing"
      echo "  --skip-fix  Run tests and report failures, but don't attempt fixes"
      echo ""
      echo "Environment:"
      echo "  MAX_ITER    Max loop iterations (default: 5)"
      exit 0
      ;;
  esac
done

# ── Logging ─────────────────────────────────────────────────
mkdir -p "$EVIDENCE_DIR"

log() {
    local level="$1"
    shift
    local ts
    ts="$(date '+%H:%M:%S')"
    echo "[$ts] [$level] $*" | tee -a "$LOG_FILE"
}

log_raw() {
    echo "$*" | tee -a "$LOG_FILE"
}

# ── Dependency check ────────────────────────────────────────
check_deps() {
    local missing=false
    local deps=(
        "$SCRIPT_DIR/run_all_tests.sh"
        "$SCRIPT_DIR/parse_test_results.py"
        "$SCRIPT_DIR/git_safety.sh"
        "$SCRIPT_DIR/fix_agent.sh"
        "$SCRIPT_DIR/notify.sh"
    )

    for dep in "${deps[@]}"; do
        if [ ! -f "$dep" ]; then
            log "ERROR" "Missing dependency: $dep"
            missing=true
        elif [ ! -x "$dep" ]; then
            log "WARN" "Dependency not executable: $dep"
            missing=true
        fi
    done

    if [ "$missing" = true ]; then
        log "FATAL" "One or more critical scripts are missing or not executable."
        exit 1
    fi
}

# ── Dry-run mode ────────────────────────────────────────────
do_dry_run() {
    log "INFO" "DRY-RUN mode — no commands will be executed"
    log_raw ""

    for ((i = 1; i <= MAX_ITER; i++)); do
        local steps="run_all_tests.sh, parse_test_results.py"
        if [ "$SKIP_FIX" = false ]; then
            steps="$steps, fix_agent.sh"
        else
            steps="$steps, (skip-fix)"
        fi
        log "DRY-RUN" "Iteration $i/$MAX_ITER: $steps"
    done

    log_raw ""
    log "DRY-RUN" "Would exit 0 if all tests pass, else fail after MAX_ITER"
    exit 0
}

# ── Summary ─────────────────────────────────────────────────
print_summary() {
    local iterations="$1"
    local status="$2"
    local end_time
    end_time="$(date +%s)"
    local elapsed=$((end_time - START_TIME))
    local mins=$((elapsed / 60))
    local secs=$((elapsed % 60))

    log_raw ""
    log_raw "╔══════════════════════════════════╗"
    log_raw "║     TEST-FIX LOOP SUMMARY       ║"
    log_raw "╠══════════════════════════════════╣"
    printf "║ Iterations: %-2s/%-2s                ║\n" "$iterations" "$MAX_ITER" | tee -a "$LOG_FILE"
    printf "║ Final status: %-18s║\n" "$status" | tee -a "$LOG_FILE"
    printf "║ Total time: %dm %ds              ║\n" "$mins" "$secs" | tee -a "$LOG_FILE"
    log_raw "╚══════════════════════════════════╝"
    log_raw ""
}

# ── Signal handler ──────────────────────────────────────────
cleanup() {
    local exit_code=$?
    log "WARN" "Interrupted by user (SIGINT/SIGTERM)"
    print_summary "$iter" "INTERRUPTED"
    exit "$exit_code"
}
trap cleanup SIGINT SIGTERM

# ── Main loop ───────────────────────────────────────────────
main() {
    check_deps

    if [ "$DRY_RUN" = true ]; then
        do_dry_run
    fi

    if [ "$SKIP_FIX" = true ]; then
        log "INFO" "SKIP-FIX mode — will report failures but not attempt fixes"
    fi

    log "INFO" "Starting test-fix loop (MAX_ITER=$MAX_ITER)"
    log_raw ""

    local iter=0
    local final_status="FAIL"

    while [ "$iter" -lt "$MAX_ITER" ]; do
        iter=$((iter + 1))
        log "INFO" "=== Iteration $iter/$MAX_ITER ==="

        # ── Step 1: Run all tests ──────────────────────────
        log "STEP" "Running all tests..."
        set +e
        bash "$SCRIPT_DIR/run_all_tests.sh" 2>&1 | tee -a "$LOG_FILE"
        local run_exit=${PIPESTATUS[0]}
        set -e

        # ── Step 2: Check if all passed ────────────────────
        if [ "$run_exit" -eq 0 ]; then
            log "PASS" "ALL TESTS PASSED"
            final_status="PASS"
            print_summary "$iter" "$final_status"
            exit 0
        fi

        log "FAIL" "Some tests failed (exit=$run_exit)"

        # ── Step 3: Parse failures ─────────────────────────
        log "STEP" "Parsing test failures..."

        # Find the latest test-run JSON
        local latest_run
        latest_run=$(ls -t "$EVIDENCE_DIR"/test-run-*.json 2>/dev/null | head -1 || true)

        if [ -z "$latest_run" ]; then
            log "ERROR" "No test-run JSON found in $EVIDENCE_DIR"
            final_status="ERROR"
            print_summary "$iter" "$final_status"
            exit 1
        fi

        log "INFO" "Using latest run: $latest_run"

        set +e
        python "$SCRIPT_DIR/parse_test_results.py" \
            --input "$latest_run" \
            --output "$EVIDENCE_DIR/latest-failures.json" 2>&1 | tee -a "$LOG_FILE"
        local parse_exit=${PIPESTATUS[0]}
        set -e

        if [ "$parse_exit" -ne 0 ]; then
            log "ERROR" "Failed to parse test results"
            final_status="ERROR"
            print_summary "$iter" "$final_status"
            exit 1
        fi

        # ── Step 4: Skip fix if --skip-fix ─────────────────
        if [ "$SKIP_FIX" = true ]; then
            log "INFO" "SKIP-FIX: Not attempting fix (--skip-fix active)"
            log "INFO" "Failures detected — would fix in non-skip mode"
            final_status="FAILURES_DETECTED"
            print_summary "$iter" "$final_status"
            exit 1
        fi

        # ── Step 5: Git safety pre-fix ─────────────────────
        log "STEP" "Git safety: pre-fix snapshot..."
        set +e
        bash "$SCRIPT_DIR/git_safety.sh" pre-fix 2>&1 | tee -a "$LOG_FILE"
        local git_exit=${PIPESTATUS[0]}
        set -e

        if [ "$git_exit" -ne 0 ]; then
            log "WARN" "Git pre-fix snapshot had issues (exit=$git_exit), continuing..."
        fi

        # ── Step 6: Run fix agent ──────────────────────────
        log "STEP" "Running fix agent (FIX_ITERATION=$iter)..."
        set +e
        FIX_ITERATION="$iter" bash "$SCRIPT_DIR/fix_agent.sh" 2>&1 | tee -a "$LOG_FILE"
        local fix_exit=${PIPESTATUS[0]}
        set -e

        # ── Step 7: Check fix agent result ─────────────────
        if [ "$fix_exit" -eq 1 ]; then
            log "FAIL" "FIX AGENT FAILED at iteration $iter"
            log "STEP" "Rolling back to previous state..."
            set +e
            bash "$SCRIPT_DIR/git_safety.sh" rollback 2>&1 | tee -a "$LOG_FILE"
            set -e
            log "INFO" "ROLLED BACK to previous state"
            final_status="FIX_FAILED"
            print_summary "$iter" "$final_status"
            exit 1
        fi

        if [ "$fix_exit" -eq 0 ]; then
            log "PASS" "Fix agent completed successfully"
        else
            log "WARN" "Fix agent exited with code $fix_exit (continuing loop)"
        fi

        # ── Step 8: Notify ─────────────────────────────────
        log "STEP" "Sending progress notification..."
        set +e
        bash "$SCRIPT_DIR/notify.sh" "IN_PROGRESS" "$iter" "$MAX_ITER" 2>&1 | tee -a "$LOG_FILE" || true
        set -e

        log_raw ""
    done

    # ── Max iterations reached ──────────────────────────────
    log "FAIL" "MAX ITERATIONS REACHED ($MAX_ITER). Manual intervention required."
    log "STEP" "Rolling back to previous state..."
    set +e
    bash "$SCRIPT_DIR/git_safety.sh" rollback 2>&1 | tee -a "$LOG_FILE" || true
    set -e
    final_status="MAX_ITERATIONS"
    print_summary "$iter" "$final_status"
    exit 1
}

main "$@"
