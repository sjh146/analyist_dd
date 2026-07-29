#!/bin/bash
# agent_monitor.sh — 3-minute agent task monitor
# Usage: bash scripts/agent_monitor.sh <task_id> [max_checks=5] [interval=180]
# Example: bash scripts/agent_monitor.sh bg_abc123 5 180

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT_DIR="$PROJECT_ROOT/reports"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="$REPORT_DIR/agent_monitor_${TIMESTAMP}.log"
TASK_ID="${1:-}"
MAX_CHECKS="${2:-5}"
INTERVAL="${3:-180}"  # 3 minutes in seconds

mkdir -p "$REPORT_DIR"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

if [ -z "$TASK_ID" ]; then
    echo -e "${RED}ERROR: Usage: $0 <task_id> [max_checks=5] [interval=180]${NC}"
    echo "  task_id: Background task ID (e.g., bg_abc123)"
    echo "  max_checks: Maximum number of polls (default: 5, total ~15 min)"
    echo "  interval: Seconds between polls (default: 180)"
    exit 1
fi

log "${CYAN}Starting monitor for task: $TASK_ID${NC}"
log "Max checks: $MAX_CHECKS, Interval: ${INTERVAL}s"

for ((i=1; i<=MAX_CHECKS; i++)); do
    log "${YELLOW}[Check $i/$MAX_CHECKS] Polling $TASK_ID...${NC}"
    
    # Get task status via the session search mechanism
    # Try to get status from .omo/evidence/ files or running processes
    if command -v opencode &>/dev/null; then
        # Attempt to check via opencode CLI
        STATUS=$(opencode task status "$TASK_ID" 2>/dev/null || echo "unknown")
        log "Status: $STATUS"
    fi
    
    # Check if there's a completion flag or evidence file
    EVIDENCE_FILE="$PROJECT_ROOT/.omo/evidence/task-${TASK_ID}.log"
    if [ -f "$EVIDENCE_FILE" ]; then
        log "${GREEN}✅ Task $TASK_ID has evidence file${NC}"
    fi
    
    # Basic process check - does the opencode process still exist?
    if pgrep -f "opencode.*$TASK_ID" > /dev/null 2>&1; then
        log "Agent process still running"
    fi
    
    # Print a progress indicator
    echo -ne "\r  ⏳ Waiting... ($i/$MAX_CHECKS)"
    
    if [ "$i" -lt "$MAX_CHECKS" ]; then
        sleep "$INTERVAL"
    fi
done

echo ""
log "${GREEN}=== Monitor Complete ===${NC}"
log "Task: $TASK_ID"
log "Checks performed: $MAX_CHECKS"
log "Log: $LOG_FILE"

# Print final summary
echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  Agent Monitor Summary${NC}"
echo -e "${CYAN}========================================${NC}"
echo "  Task ID:     $TASK_ID"
echo "  Checks:      $MAX_CHECKS (every ${INTERVAL}s)"
echo "  Total time:  $((MAX_CHECKS * INTERVAL / 60)) minutes"
echo "  Log file:    $LOG_FILE"
echo ""
echo -e "${YELLOW}  Next step: Check background_output() for results${NC}"
echo -e "${YELLOW}  Command: background_output(task_id=\"$TASK_ID\")${NC}"
echo -e "${CYAN}========================================${NC}"
