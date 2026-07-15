#!/usr/bin/env bash
# Inject intentional failure into test suite to validate fix agent recovery.
# Usage: bash scripts/inject_failure.sh {inject|restore|verify|verify-pipeline}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EVIDENCE_DIR="$PROJECT_DIR/.omo/evidence"

# The actual test file in this project
TEST_FILE="services/xgboost-ml/tests/test_model.py"
TEST_FILE_ABS="$PROJECT_DIR/$TEST_FILE"
BACKUP_FILE="$EVIDENCE_DIR/test_model_backup.py"

# Marker comment used to detect injection (unique enough to avoid false matches)
INJECT_MARKER="# INJECTED_BY_INJECT_FAILURE_SH_DO_NOT_REMOVE_MANUALLY"

# ── Helpers ──────────────────────────────────────────────────────────
log() {
    local level="$1"
    shift
    echo "[$level] $*"
}

# ── Mode: inject ─────────────────────────────────────────────────────
do_inject() {
    log "INFO" "Injecting failure into $TEST_FILE ..."

    # Create evidence dir if needed
    mkdir -p "$EVIDENCE_DIR"

    # Backup the original test file (idempotent: only backup once)
    if [ ! -f "$BACKUP_FILE" ]; then
        cp "$TEST_FILE_ABS" "$BACKUP_FILE"
        log "INFO" "Backup created at $BACKUP_FILE"
    else
        log "INFO" "Backup already exists — skipping backup"
    fi

    # Check if already injected (idempotent)
    if grep -qF "$INJECT_MARKER" "$TEST_FILE_ABS" 2>/dev/null; then
        log "WARN" "Failure already injected — skipping (use 'restore' first if needed)"
        return 0
    fi

    # Append a deliberately failing test with the marker
    cat >> "$TEST_FILE_ABS" << EOF


$INJECT_MARKER
def test_intentionally_failing():
    """This test intentionally fails to validate fix agent recovery."""
    assert False, "This failure was intentionally injected for testing"
EOF

    log "PASS" "Injected failing test: test_intentionally_failing"
    log "INFO" "Run 'bash $0 restore' to revert"
}

# ── Mode: restore ────────────────────────────────────────────────────
do_restore() {
    log "INFO" "Restoring original test file..."

    if [ -f "$BACKUP_FILE" ]; then
        cp "$BACKUP_FILE" "$TEST_FILE_ABS"
        rm -f "$BACKUP_FILE"
        log "PASS" "Original test restored from backup"
    else
        log "WARN" "No backup found at $BACKUP_FILE"
        # Check if injection marker is still present — remove it manually
        if grep -qF "$INJECT_MARKER" "$TEST_FILE_ABS" 2>/dev/null; then
            log "WARN" "Injection marker found but no backup — removing injected lines via sed"
            # Remove from marker to end of file
            sed -i "/$INJECT_MARKER/,\$d" "$TEST_FILE_ABS"
            log "PASS" "Injected lines removed via sed"
        else
            log "INFO" "No injection found — file is clean"
        fi
    fi

    # Verify no marker remains
    if grep -qF "$INJECT_MARKER" "$TEST_FILE_ABS" 2>/dev/null; then
        log "ERROR" "Injection marker still present after restore!"
        return 1
    fi

    log "PASS" "Restore complete — test file is clean"
}

# ── Mode: verify ─────────────────────────────────────────────────────
do_verify() {
    log "INFO" "=== Verifying inject/restore cycle ==="
    local exit_code=0

    # 1. Inject
    log "STEP" "Injecting failure..."
    do_inject

    # 2. Confirm injection
    if grep -qF "test_intentionally_failing" "$TEST_FILE_ABS"; then
        log "PASS" "Injection confirmed: test_intentionally_failing found in file"
    else
        log "FAIL" "Injection failed: test_intentionally_failing NOT found"
        exit_code=1
    fi

    # 3. Restore
    log "STEP" "Restoring..."
    do_restore

    # 4. Confirm restoration
    if grep -qF "test_intentionally_failing" "$TEST_FILE_ABS"; then
        log "FAIL" "Restore failed: test_intentionally_failing still present"
        exit_code=1
    else
        log "PASS" "Restore confirmed: test_intentionally_failing removed"
    fi

    # 5. Verify original tests are intact
    if grep -qF "test_init_default_params" "$TEST_FILE_ABS"; then
        log "PASS" "Original tests intact"
    else
        log "FAIL" "Original tests damaged!"
        exit_code=1
    fi

    log "INFO" "=== Verify cycle complete ==="
    return "$exit_code"
}

# ── Mode: verify-pipeline ────────────────────────────────────────────
do_verify_pipeline() {
    log "INFO" "=== Verifying pipeline detects injected failure ==="

    # 1. Inject
    do_inject

    # 2. Run tests — expect failure
    log "STEP" "Running tests (expecting failure)..."
    set +e
    docker compose run --rm -v "$(pwd)/services/xgboost-ml/tests:/app/tests" xgboost-ml python -m pytest tests/test_model.py::test_intentionally_failing -v --tb=line 2>&1
    local pytest_exit=$?
    set -e

    if [ "$pytest_exit" -eq 0 ]; then
        log "FAIL" "Injected test PASSED — it should have failed!"
        do_restore
        return 1
    fi
    log "PASS" "Injected test correctly failed (exit=$pytest_exit)"

    # 3. Run full test suite with JUnit XML report
    log "STEP" "Running full test suite with JUnit XML report..."
    set +e
    docker compose run --rm \
        -v "$(pwd)/services/xgboost-ml/tests:/app/tests" \
        -v "$(pwd)/.omo/evidence:/app/.omo/evidence" \
        xgboost-ml python -m pytest tests/test_model.py -v --tb=line --junitxml=.omo/evidence/pytest-report.xml 2>&1 | tail -10
    local full_exit=$?
    set -e

    # 4. Parse results
    log "STEP" "Parsing test results..."
    mkdir -p "$EVIDENCE_DIR"
    set +e
    python "$SCRIPT_DIR/parse_test_results.py" \
        --input "$EVIDENCE_DIR/pytest-report.xml" \
        --output "$EVIDENCE_DIR/latest-failures.json" 2>&1
    local parse_exit=$?
    set -e

    if [ "$parse_exit" -ne 0 ]; then
        log "FAIL" "Parse step failed (exit=$parse_exit)"
        do_restore
        return 1
    fi
    log "PASS" "Parse step completed"

    # 5. Check failures detected
    log "STEP" "Checking failure detection..."
    python3 -c "
import json
with open('$EVIDENCE_DIR/latest-failures.json') as f:
    data = json.load(f)
if not data.get('all_passed', True):
    failed = data.get('failed_tests', [])
    names = [t.get('test_name', '') for t in failed]
    print(f'PASS: Failures detected: {len(failed)} failed test(s)')
    for n in names:
        print(f'  - {n}')
    # Check our injected test is in the list
    if any('test_intentionally_failing' in n for n in names):
        print('PASS: Injected test correctly identified in failures')
    else:
        print('FAIL: Injected test NOT found in failure list')
        exit(1)
else:
    print('FAIL: No failures detected — parse bug?')
    exit(1)
"
    local check_exit=$?
    set +e

    # 6. Restore
    do_restore

    if [ "$check_exit" -ne 0 ]; then
        log "FAIL" "Pipeline verification failed"
        return 1
    fi

    log "PASS" "=== Pipeline verification complete ==="
    return 0
}

# ── Main dispatch ────────────────────────────────────────────────────
MODE="${1:-help}"

case "$MODE" in
    inject)
        do_inject
        ;;
    restore)
        do_restore
        ;;
    verify)
        do_verify
        ;;
    verify-pipeline)
        do_verify_pipeline
        ;;
    help|--help|-h)
        echo "Usage: $0 {inject|restore|verify|verify-pipeline}"
        echo ""
        echo "  inject           Inject a deliberately failing test into test_model.py"
        echo "  restore          Restore test_model.py from backup (or remove injected lines)"
        echo "  verify           Run inject then restore and confirm both work"
        echo "  verify-pipeline  Full pipeline test: inject → run tests → parse → detect → restore"
        exit 0
        ;;
    *)
        echo "Error: Unknown mode '$MODE'"
        echo "Usage: $0 {inject|restore|verify|verify-pipeline}"
        exit 1
        ;;
esac
