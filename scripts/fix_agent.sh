#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EVIDENCE_DIR="$PROJECT_DIR/.omo/evidence"
FAILURES_FILE="$EVIDENCE_DIR/latest-failures.json"
LOG_FILE="$EVIDENCE_DIR/fix-agent.log"
ISO_TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S")

FIX_ITERATION="${FIX_ITERATION:-0}"
FIX_ITERATION=$((FIX_ITERATION + 1))

log() {
    local level="$1"
    shift
    echo "[$ISO_TIMESTAMP] [$level] $*" >> "$LOG_FILE"
}

if [ ! -f "$FAILURES_FILE" ]; then
    echo "ERROR: Failures file not found: $FAILURES_FILE" >&2
    log "ERROR" "Failures file not found: $FAILURES_FILE"
    echo '{"iteration":'"$FIX_ITERATION"',"timestamp":"'"$ISO_TIMESTAMP"'","failures_found":0,"status":"error","message":"Failures file not found"}'
    exit 1
fi

if ! grep -q '"all_passed"' "$FAILURES_FILE" 2>/dev/null; then
    echo "ERROR: Invalid or malformed JSON in $FAILURES_FILE (missing 'all_passed')" >&2
    log "ERROR" "Invalid JSON in $FAILURES_FILE"
    echo '{"iteration":'"$FIX_ITERATION"',"timestamp":"'"$ISO_TIMESTAMP"'","failures_found":0,"status":"error","message":"Invalid JSON"}'
    exit 1
fi

ALL_PASSED=""
if grep -q '"all_passed"[[:space:]]*:[[:space:]]*true' "$FAILURES_FILE" 2>/dev/null; then
    ALL_PASSED="true"
elif grep -q '"all_passed"[[:space:]]*:[[:space:]]*false' "$FAILURES_FILE" 2>/dev/null; then
    ALL_PASSED="false"
fi

if [ "$ALL_PASSED" = "true" ]; then
    log "INFO" "No failures to fix (all_passed=true)"
    echo '{"iteration":'"$FIX_ITERATION"',"timestamp":"'"$ISO_TIMESTAMP"'","failures_found":0,"status":"passed"}'
    exit 0
fi

FAILURE_COUNT=$(grep -c '"test_name"' "$FAILURES_FILE" 2>/dev/null || echo 0)

if [ "$FAILURE_COUNT" -eq 0 ]; then
    log "INFO" "all_passed=false but no failed_tests entries found"
    echo '{"iteration":'"$FIX_ITERATION"',"timestamp":"'"$ISO_TIMESTAMP"'","failures_found":0,"status":"passed"}'
    exit 0
fi

if [ "$FIX_ITERATION" -ge 5 ]; then
    log "ERROR" "MAX FIX ATTEMPTS REACHED (iteration=$FIX_ITERATION)"
    echo "MAX FIX ATTEMPTS REACHED" >&2
    echo '{"iteration":'"$FIX_ITERATION"',"timestamp":"'"$ISO_TIMESTAMP"'","failures_found":'"$FAILURE_COUNT"',"status":"max-attempts-reached"}'
    exit 1
fi

log "INFO" "Fix iteration $FIX_ITERATION — $FAILURE_COUNT failure(s) found"

# Parse failures: extract each JSON object from the failed_tests array
# and log its fields. Uses a temp file to avoid multi-line string issues.
TMPFILE=$(mktemp)
grep -o '{[^}]*}' "$FAILURES_FILE" > "$TMPFILE" 2>/dev/null || true
ENTRY_NUM=0
while IFS= read -r entry; do
    entry="$(echo "$entry" | tr -d '\n')"
    if echo "$entry" | grep -q '"test_name"'; then
        ENTRY_NUM=$((ENTRY_NUM + 1))
        TNAME=$(echo "$entry" | grep -o '"test_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"test_name"[[:space:]]*:[[:space:]]*"\(.*\)"/\1/')
        TFILE=$(echo "$entry" | grep -o '"file"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"file"[[:space:]]*:[[:space:]]*"\(.*\)"/\1/')
        TLINE=$(echo "$entry" | grep -o '"line"[[:space:]]*:[[:space:]]*[0-9]*' | head -1 | sed 's/.*"line"[[:space:]]*:[[:space:]]*\([0-9]*\)/\1/')
        TERR=$(echo "$entry" | grep -o '"error"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"error"[[:space:]]*:[[:space:]]*"\(.*\)"/\1/')
        log "FAILURE" "FAILURE[$ENTRY_NUM] test=$TNAME file=$TFILE line=$TLINE error=$TERR"
    fi
done < "$TMPFILE"
rm -f "$TMPFILE"

echo '{"iteration":'"$FIX_ITERATION"',"timestamp":"'"$ISO_TIMESTAMP"'","failures_found":'"$FAILURE_COUNT"',"status":"needs-fix"}'

export FIX_ITERATION
