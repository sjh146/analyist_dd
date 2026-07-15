#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# emergency_revert.sh — Roll back N commits when fix loop goes wrong
# Usage: emergency_revert.sh [count]
#   count: number of commits to revert (default: 1, 0 = dry-run)
# ─────────────────────────────────────────────────────────────

COUNT="${1:-1}"

# Validate count is a non-negative integer
if ! [[ "$COUNT" =~ ^[0-9]+$ ]]; then
  echo "{\"status\": \"failure\", \"error\": \"count must be a non-negative integer, got: ${COUNT}\"}"
  exit 1
fi

# Check we're in a git repo
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "{\"status\": \"failure\", \"error\": \"not a git repository\"}"
  exit 1
fi

HEAD_BEFORE=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

# Count available ancestor commits
TOTAL_COMMITS=$(git rev-list --count HEAD 2>/dev/null || echo "0")

if [ "$COUNT" -eq 0 ]; then
  # Dry-run mode — just print what would happen
  cat <<EOF
{"mode": "dry-run", "status": "success", "message": "would revert ${COUNT} commits", "head": "${HEAD_BEFORE}", "total_commits": ${TOTAL_COMMITS}}
EOF
  exit 0
fi

if [ "$COUNT" -gt "$TOTAL_COMMITS" ]; then
  echo "{\"status\": \"failure\", \"error\": \"cannot revert ${COUNT} commits; only ${TOTAL_COMMITS} in history\"}"
  exit 1
fi

# Get commit messages being reverted (before we destroy them)
REVERTED_LOG=$(git log --oneline -"${COUNT}" HEAD 2>/dev/null || true)

# Reset
if ! git reset --hard "HEAD~${COUNT}" 2>/dev/null; then
  echo "{\"status\": \"failure\", \"error\": \"git reset --hard HEAD~${COUNT} failed\"}"
  exit 1
fi

HEAD_AFTER=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

# Tag the revert point
TAG="emergency-revert-$(date +%s)"
git tag "$TAG" 2>/dev/null || true

# Build summary of reverted changes
SUMMARY=$(echo "$REVERTED_LOG" | awk '{printf "%s\\n", $0}' | paste -sd '' -)

cat <<EOF
{"status": "success", "reverted_commits": ${COUNT}, "head_before": "${HEAD_BEFORE}", "head_after": "${HEAD_AFTER}", "tag": "${TAG}", "summary": "${SUMMARY}"}
EOF
