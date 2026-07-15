#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# git_safety.sh — Safety wrapper for automated fix loops
# Modes: pre-fix | rollback | status
# ─────────────────────────────────────────────────────────────

MODE="${1:-help}"

case "$MODE" in
  pre-fix)
    # Stash any uncommitted changes (including untracked)
    STASH_MSG="pre-fix-$(date +%s)"
    if ! git stash push -u -m "$STASH_MSG" 2>/dev/null; then
      # Nothing to stash — that's fine
      STASH_REF="null"
    else
      STASH_REF="refs/stash"
    fi

    # Record current HEAD
    HEAD_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

    # Create tag
    TAG="auto-fix-attempt-$(date +%s)"
    if ! git tag "$TAG" HEAD 2>/dev/null; then
      TAG="null"
    fi

    cat <<EOF
{"mode": "pre-fix", "stash_ref": ${STASH_REF}, "head": "${HEAD_HASH}", "tag": "${TAG}"}
EOF
    ;;

  rollback)
    HEAD_BEFORE=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

    # Check if last commit message indicates an auto-fix
    LAST_MSG=$(git log -1 --pretty=%B 2>/dev/null || echo "")
    ROLLBACK_MSG=""

    if echo "$LAST_MSG" | grep -qi "auto-fix\|fix-attempt"; then
      git reset --hard HEAD~1 2>/dev/null || {
        cat <<EOF
{"mode": "rollback", "status": "failure", "message": "no commit to reset from HEAD~1"}
EOF
        exit 1
      }
      ROLLBACK_MSG="rolled back auto-fix commit"
    else
      ROLLBACK_MSG="no auto-fix commit found; skipping reset"
    fi

    # Restore stashed changes
    STASH_LIST=$(git stash list 2>/dev/null | head -1 || true)
    if [ -n "$STASH_LIST" ]; then
      if git stash pop 2>/dev/null; then
        ROLLBACK_MSG="${ROLLBACK_MSG}; stashed changes restored"
      else
        ROLLBACK_MSG="${ROLLBACK_MSG}; stash pop FAILED — manual recovery needed"
        echo "[WARN] git stash pop failed. Available stashes:" >&2
        git stash list >&2
      fi
    else
      ROLLBACK_MSG="${ROLLBACK_MSG}; no stashed changes to restore"
    fi

    HEAD_AFTER=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

    cat <<EOF
{"mode": "rollback", "status": "success", "message": "${ROLLBACK_MSG}", "head_before": "${HEAD_BEFORE}", "head_after": "${HEAD_AFTER}"}
EOF
    ;;

  status)
    TAG_COUNT=$(git tag -l "auto-fix-*" 2>/dev/null | wc -l)
    STASH_COUNT=$(git stash list 2>/dev/null | wc -l)
    UNCOMMITTED=$(git status --porcelain 2>/dev/null | wc -l)

    cat <<EOF
{"mode": "status", "auto_fix_tags": ${TAG_COUNT}, "stash_count": ${STASH_COUNT}, "uncommitted_files": ${UNCOMMITTED}}
EOF
    ;;

  help|--help|-h)
    cat <<USAGE
Usage: git_safety.sh <mode>

Modes:
  pre-fix   Stash changes, record HEAD, create auto-fix tag
  rollback  Reset last auto-fix commit, restore stash
  status    Show current safety state (tags, stashes, uncommitted)

All modes output valid JSON.
USAGE
    ;;

  *)
    echo "{\"error\": \"unknown mode: ${MODE}\", \"usage\": \"pre-fix | rollback | status | help\"}"
    exit 1
    ;;
esac
