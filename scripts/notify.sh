#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# notify.sh — Notification dispatcher for the test-fix loop
# Supports Slack webhook (via SLACK_WEBHOOK_URL) and stdout fallback.
#
# Usage:
#   SLACK_WEBHOOK_URL="" bash scripts/notify.sh <STATUS> <ITER> <MAX_ITER> [PASSED] [FAILED] [DURATION]
#
# Arguments:
#   STATUS    - one of: PASS, FAIL, IN_PROGRESS, MAX_ATTEMPT
#   ITER      - current iteration number
#   MAX_ITER  - max iterations
#   PASSED    - tests passed (optional)
#   FAILED    - tests failed (optional)
#   DURATION  - elapsed time (optional)
#
# Env:
#   SLACK_WEBHOOK_URL  - if set, POST JSON payload to Slack
#                        if empty/unset, print to stdout
# ─────────────────────────────────────────────────────────────

usage() {
  cat <<EOF
Usage: SLACK_WEBHOOK_URL="" bash scripts/notify.sh <STATUS> <ITER> <MAX_ITER> [PASSED] [FAILED] [DURATION]

Arguments:
  STATUS     PASS | FAIL | IN_PROGRESS | MAX_ATTEMPT
  ITER       Current iteration number
  MAX_ITER   Max iterations
  PASSED     Tests passed (optional)
  FAILED     Tests failed (optional)
  DURATION   Elapsed time (optional)

Examples:
  SLACK_WEBHOOK_URL="" bash scripts/notify.sh PASS 3 5 19 0 "2m34s"
  SLACK_WEBHOOK_URL="https://hooks.slack.com/..." bash scripts/notify.sh IN_PROGRESS 2 5 18 1 "1m34s"
EOF
  exit 1
}

# ── Parse arguments ──────────────────────────────────────────

STATUS="${1:-}"
ITER="${2:-}"
MAX_ITER="${3:-}"
PASSED="${4:-}"
FAILED="${5:-}"
DURATION="${6:-}"

if [[ -z "$STATUS" || -z "$ITER" || -z "$MAX_ITER" ]]; then
  usage
fi

# ── Setup paths ──────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EVIDENCE_DIR="$PROJECT_DIR/.omo/evidence"
LOG_FILE="$EVIDENCE_DIR/notify.log"

mkdir -p "$EVIDENCE_DIR"

# ── Timestamp ────────────────────────────────────────────────

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# ── Build summary line ───────────────────────────────────────

SUMMARY="Status: $STATUS | Iterations: $ITER/$MAX_ITER"
if [[ -n "$PASSED" && -n "$FAILED" ]]; then
  TOTAL=$((PASSED + FAILED))
  SUMMARY="$SUMMARY | Tests: $PASSED/$TOTAL passed"
fi
if [[ -n "$DURATION" ]]; then
  SUMMARY="$SUMMARY | Duration: $DURATION"
fi

# ── Log to file ──────────────────────────────────────────────

echo "[$TIMESTAMP] $SUMMARY" >> "$LOG_FILE"

# ── Slack webhook ────────────────────────────────────────────

if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  # Build Slack blocks
  BLOCK_TEXT="*Test-Fix Loop Update*\nStatus: $STATUS\nIteration: $ITER/$MAX_ITER"
  if [[ -n "$PASSED" && -n "$FAILED" ]]; then
    TOTAL=$((PASSED + FAILED))
    BLOCK_TEXT="$BLOCK_TEXT\nTests: $PASSED passed, $FAILED failed"
  fi
  if [[ -n "$DURATION" ]]; then
    BLOCK_TEXT="$BLOCK_TEXT\nDuration: $DURATION"
  fi

  PAYLOAD=$(cat <<EOF
{
  "text": "[Test-Fix Loop] $STATUS - Iteration $ITER/$MAX_ITER",
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "$BLOCK_TEXT"
      }
    }
  ]
}
EOF
)

  if command -v curl &>/dev/null; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -X POST \
      -H "Content-Type: application/json" \
      -d "$PAYLOAD" \
      "$SLACK_WEBHOOK_URL" 2>/dev/null || echo "000")

    if [[ "$HTTP_CODE" =~ ^2[0-9][0-9]$ ]]; then
      echo "[$TIMESTAMP] Slack notification sent (HTTP $HTTP_CODE)" >> "$LOG_FILE"
    else
      echo "[$TIMESTAMP] Slack notification failed (HTTP $HTTP_CODE)" >> "$LOG_FILE"
    fi
  else
    echo "[$TIMESTAMP] curl not found — skipping Slack webhook" >> "$LOG_FILE"
  fi
else
  # ── stdout fallback ──────────────────────────────────────
  echo "[NOTIFY] $SUMMARY"
fi

exit 0
