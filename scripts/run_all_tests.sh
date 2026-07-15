#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# run_all_tests.sh — Unified test runner
# Runs Docker service check, pytest in container,
# optional host E2E test, and saves JSON results.
# ──────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EVIDENCE_DIR="$PROJECT_DIR/.omo/evidence"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
ISO_TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S")

mkdir -p "$EVIDENCE_DIR"

# ── Step 1: Check Docker services ──────────────────────────────────
echo "=== [1/3] Checking Docker services ==="
SERVICES_UP=true
SERVICE_STATUS=$(docker compose ps --format "{{.Name}} {{.Status}}" 2>&1) || {
    echo "ERROR: Cannot run 'docker compose ps'. Is Docker running?"
    SERVICES_UP=false
}

if [ "$SERVICES_UP" = true ]; then
    if [ -z "$SERVICE_STATUS" ]; then
        echo "ERROR: No Docker services found (docker compose ps returned empty)"
        SERVICES_UP=false
    else
        while IFS= read -r line; do
            [ -z "$line" ] && continue
            name="${line%% *}"
            status="${line#* }"
            if [[ "$status" != "Up"* ]]; then
                echo "ERROR: Service '$name' is not Up (status: $status)"
                SERVICES_UP=false
            else
                echo "  OK: $name — $status"
            fi
        done <<< "$SERVICE_STATUS"
    fi
fi

if [ "$SERVICES_UP" = false ]; then
    echo "FATAL: One or more Docker services are not running."
    # Write partial result
    cat > "$EVIDENCE_DIR/test-run-$TIMESTAMP.json" <<JSONEOF
{
  "timestamp": "$ISO_TIMESTAMP",
  "docker_services_up": false,
  "pytest": null,
  "e2e": null,
  "overall": "FAIL"
}
JSONEOF
    exit 1
fi

# ── Step 2: Run pytest inside Docker ───────────────────────────────
echo ""
echo "=== [2/3] Running pytest inside xgboost-ml container ==="

PYTEST_EXIT_CODE=0
PYTEST_PASSED=0
PYTEST_FAILED=0
PYTEST_TOTAL=0

# Try --json-report first, fall back to --junitxml
PYTEST_OUTPUT=$(mktemp)
set +e
docker compose exec -T xgboost-ml python -m pytest tests/ -v --tb=short --json-report=.omo/evidence/pytest-report.json 2>&1 | tee "$PYTEST_OUTPUT"
PYTEST_EXIT_CODE=${PIPESTATUS[0]}
set -e

if [ $PYTEST_EXIT_CODE -eq 2 ] && grep -qi "unrecognized arguments.*json-report" "$PYTEST_OUTPUT"; then
    echo ""
    echo "  --json-report not supported, falling back to --junitxml"
    set +e
    docker compose exec -T xgboost-ml python -m pytest tests/ -v --tb=short --junitxml=.omo/evidence/pytest-report.xml 2>&1 | tee "$PYTEST_OUTPUT"
    PYTEST_EXIT_CODE=${PIPESTATUS[0]}
    set -e
fi

# Parse pytest output for summary line like "= 5 passed in 0.12s ="
SUMMARY_LINE=$(grep -oP '^\s*\d+ passed.*$' "$PYTEST_OUTPUT" | tail -1 || true)
if [ -z "$SUMMARY_LINE" ]; then
    # Try alternate format: "= 5 passed, 2 failed in 0.12s ="
    SUMMARY_LINE=$(grep -oP '=.*\d+ passed.*=.*' "$PYTEST_OUTPUT" | tail -1 || true)
fi

if [[ "$SUMMARY_LINE" =~ ([0-9]+)\ +passed ]]; then
    PYTEST_PASSED="${BASH_REMATCH[1]}"
fi
if [[ "$SUMMARY_LINE" =~ ([0-9]+)\ +failed ]]; then
    PYTEST_FAILED="${BASH_REMATCH[1]}"
fi
PYTEST_TOTAL=$((PYTEST_PASSED + PYTEST_FAILED))
rm -f "$PYTEST_OUTPUT"

echo ""
echo "  pytest: $PYTEST_PASSED passed, $PYTEST_FAILED failed (exit=$PYTEST_EXIT_CODE)"

# ── Step 3: Run host-based E2E test (if available) ─────────────────
echo ""
echo "=== [3/3] Running E2E test (host) ==="

E2E_EXIT_CODE=0
E2E_PASSED=0
E2E_FAILED=0

E2E_SCRIPT="$PROJECT_DIR/tests/test_e2e_pipeline.py"
if [ -f "$E2E_SCRIPT" ] && command -v python &>/dev/null; then
    E2E_OUTPUT=$(mktemp)
    set +e
    python "$E2E_SCRIPT" -v 2>&1 | tee "$E2E_OUTPUT"
    E2E_EXIT_CODE=${PIPESTATUS[0]}
    set -e

    if grep -qi "passed" "$E2E_OUTPUT"; then
        E2E_PASSED=1
    fi
    if [ $E2E_EXIT_CODE -ne 0 ]; then
        E2E_FAILED=1
    fi
    rm -f "$E2E_OUTPUT"
    echo "  E2E: exit=$E2E_EXIT_CODE"
else
    echo "  E2E: test file not found — skipping"
fi

# ── Step 4: Determine overall result ───────────────────────────────
OVERALL="PASS"
if [ "$SERVICES_UP" != true ] || [ $PYTEST_EXIT_CODE -ne 0 ] || [ $E2E_EXIT_CODE -ne 0 ]; then
    OVERALL="FAIL"
fi

# ── Step 5: Write result JSON ──────────────────────────────────────
RESULT_FILE="$EVIDENCE_DIR/test-run-$TIMESTAMP.json"
cat > "$RESULT_FILE" <<JSONEOF
{
  "timestamp": "$ISO_TIMESTAMP",
  "docker_services_up": $SERVICES_UP,
  "pytest": {
    "passed": $PYTEST_PASSED,
    "failed": $PYTEST_FAILED,
    "total": $PYTEST_TOTAL,
    "exit_code": $PYTEST_EXIT_CODE
  },
  "e2e": {
    "passed": $E2E_PASSED,
    "failed": $E2E_FAILED,
    "exit_code": $E2E_EXIT_CODE
  },
  "overall": "$OVERALL"
}
JSONEOF

echo ""
echo "=== Results saved to: $RESULT_FILE ==="
echo "  Overall: $OVERALL"

if [ "$OVERALL" = "FAIL" ]; then
    exit 1
fi
exit 0
