#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# loop_monitor.sh — Real-time test-fix loop progress dashboard
# ─────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EVIDENCE_DIR="$PROJECT_DIR/.omo/evidence"
LOG_FILE="$EVIDENCE_DIR/test-fix-loop.log"
FIX_LOG="$EVIDENCE_DIR/fix-agent.log"

SHOW_ERRORS=false
WATCH_MODE=false

for arg in "$@"; do
  case "$arg" in
    --errors) SHOW_ERRORS=true ;;
    --watch)  WATCH_MODE=true ;;
    --help|-h)
      echo "Usage: $0 [--errors] [--watch]"
      echo ""
      echo "  --errors  Show latest error details from fix-agent.log"
      echo "  --watch   Auto-refresh every 5 seconds (uses watch)"
      exit 0
      ;;
  esac
done

# If --watch, delegate to watch and exit
if $WATCH_MODE; then
  exec watch -n5 "$0" ${SHOW_ERRORS:+--errors}
fi

# ── Collect evidence files ──────────────────────────────────
EVIDENCE_FILES=()
if [ -d "$EVIDENCE_DIR" ]; then
  while IFS= read -r -d '' f; do
    EVIDENCE_FILES+=("$f")
  done < <(find "$EVIDENCE_DIR" -maxdepth 1 -name 'test-run-*.json' -print0 2>/dev/null | sort -z)
fi

COUNT=${#EVIDENCE_FILES[@]}

# ── No data guard ───────────────────────────────────────────
if [ "$COUNT" -eq 0 ]; then
  echo "No test runs found. Run test_fix_loop.sh first."
  exit 0
fi

# ── Parse all runs ──────────────────────────────────────────
BEST_PASSED=0
BEST_TOTAL=0
BEST_STATUS="N/A"
LATEST_PASSED=0
LATEST_TOTAL=0
LATEST_STATUS="N/A"
ITER_CURRENT=0
ITER_MAX=0

# Arrays for iteration trend
declare -a RUN_PASSED
declare -a RUN_TOTAL
declare -a RUN_STATUS
declare -a RUN_LABEL

IDX=0
for f in "${EVIDENCE_FILES[@]}"; do
  DATA=$(cat "$f")

  # Extract pytest fields (handle null)
  P_PASSED=$(echo "$DATA" | grep -o '"passed":[[:space:]]*[0-9][0-9]*' | head -1 | grep -o '[0-9][0-9]*$' || echo "")
  P_FAILED=$(echo "$DATA" | grep -o '"failed":[[:space:]]*[0-9][0-9]*' | head -1 | grep -o '[0-9][0-9]*$' || echo "")
  P_TOTAL=$(echo "$DATA" | grep -o '"total":[[:space:]]*[0-9][0-9]*' | head -1 | grep -o '[0-9][0-9]*$' || echo "")
  OVERALL=$(echo "$DATA" | grep -o '"overall":[[:space:]]*"[A-Z][A-Z]*"' | sed 's/.*"\([A-Z][A-Z]*\)".*/\1/' || echo "")

  # Defaults for null pytest
  P_PASSED=${P_PASSED:-0}
  P_FAILED=${P_FAILED:-0}
  P_TOTAL=${P_TOTAL:-0}
  OVERALL=${OVERALL:-"N/A"}

  # Timestamp for label
  TS_RAW=$(echo "$DATA" | grep -o '"timestamp"[[:space:]]*:[[:space:]]*"[^"]*"')
  TS=$(echo "$TS_RAW" | sed 's/.*"\([^"]*\)"[^"]*$/\1/')
  LABEL=$(echo "$TS" | sed 's/[T.]/ /g' | awk '{print $2}' | cut -c1-8)
  [ -z "$LABEL" ] && LABEL="run-$((IDX+1))"

  RUN_PASSED[$IDX]=$P_PASSED
  RUN_TOTAL[$IDX]=$P_TOTAL
  RUN_STATUS[$IDX]=$OVERALL
  RUN_LABEL[$IDX]="Run $((IDX+1))"

  # Track best
  if [ "$P_TOTAL" -gt 0 ] && [ "$P_PASSED" -gt "$BEST_PASSED" ]; then
    BEST_PASSED=$P_PASSED
    BEST_TOTAL=$P_TOTAL
    BEST_STATUS=$OVERALL
  elif [ "$P_TOTAL" -gt 0 ] && [ "$P_PASSED" -eq "$BEST_PASSED" ] && [ "$P_TOTAL" -gt "$BEST_TOTAL" ]; then
    BEST_TOTAL=$P_TOTAL
    BEST_STATUS=$OVERALL
  fi

  # Latest
  LATEST_PASSED=$P_PASSED
  LATEST_TOTAL=$P_TOTAL
  LATEST_STATUS=$OVERALL

  IDX=$((IDX + 1))
done

# ── Parse iteration info from log ───────────────────────────
if [ -f "$LOG_FILE" ]; then
  LAST_ITER_LINE=$(grep -o 'Iteration [0-9]*/[0-9]*' "$LOG_FILE" | tail -1)
  if [ -n "$LAST_ITER_LINE" ]; then
    ITER_CURRENT=$(echo "$LAST_ITER_LINE" | cut -d' ' -f2 | cut -d/ -f1)
    ITER_MAX=$(echo "$LAST_ITER_LINE" | cut -d/ -f2)
  fi
fi

# ── Elapsed time ────────────────────────────────────────────
ELAPSED="N/A"
if [ -f "$LOG_FILE" ]; then
  FIRST_TS=$(grep -o '\[[0-9:]*\]' "$LOG_FILE" | head -1 | tr -d '[]')
  LAST_TS=$(grep -o '\[[0-9:]*\]' "$LOG_FILE" | tail -1 | tr -d '[]')
  if [ -n "$FIRST_TS" ] && [ -n "$LAST_TS" ]; then
    # Convert HH:MM:SS to seconds
    f_h=$(echo "$FIRST_TS" | cut -d: -f1)
    f_m=$(echo "$FIRST_TS" | cut -d: -f2)
    f_s=$(echo "$FIRST_TS" | cut -d: -f3)
    l_h=$(echo "$LAST_TS" | cut -d: -f1)
    l_m=$(echo "$LAST_TS" | cut -d: -f2)
    l_s=$(echo "$LAST_TS" | cut -d: -f3)
    f_sec=$((10#$f_h * 3600 + 10#$f_m * 60 + 10#$f_s))
    l_sec=$((10#$l_h * 3600 + 10#$l_m * 60 + 10#$l_s))
    diff=$((l_sec - f_sec))
    if [ "$diff" -ge 0 ]; then
      d_min=$((diff / 60))
      d_sec=$((diff % 60))
      ELAPSED="${d_min}m ${d_sec}s"
    fi
  fi
fi

# ── Render dashboard ────────────────────────────────────────
W=38

print_line() {
  local content="$1"
  local len=${#content}
  local pad=$((W - len - 2))
  printf "║ %s%${pad}s║\n" "$content" ""
}

echo "╔$(printf '═%.0s' $(seq 1 $W))╗"
print_line "TEST-FIX LOOP MONITOR"
echo "╠$(printf '═%.0s' $(seq 1 $W))╣"
print_line "Test Runs: $COUNT"
print_line "Best: ${BEST_PASSED}/${BEST_TOTAL} passed (${BEST_STATUS})"
print_line "Latest: ${LATEST_PASSED}/${LATEST_TOTAL} passed (${LATEST_STATUS})"
if [ "$ITER_MAX" -gt 0 ]; then
  print_line "Iteration: ${ITER_CURRENT}/${ITER_MAX}"
fi
print_line "Elapsed: ${ELAPSED}"
echo "╚$(printf '═%.0s' $(seq 1 $W))╝"

# ── Iteration trend bar chart ───────────────────────────────
echo ""
echo "Iteration progress:"
for ((i=0; i<COUNT; i++)); do
  pt=${RUN_TOTAL[$i]}
  ps=${RUN_PASSED[$i]}
  st=${RUN_STATUS[$i]}

  if [ "$pt" -gt 0 ]; then
    filled=$((ps * 20 / pt))
  else
    filled=0
  fi
  [ "$filled" -gt 20 ] && filled=20

  bar=""
  for ((b=0; b<filled; b++)); do bar="${bar}█"; done
  for ((b=filled; b<20; b++)); do bar="${bar}░"; done

  echo "  ${RUN_LABEL[$i]}: $bar $ps/$pt $st"
done

# ── Error summary (--errors flag) ───────────────────────────
if $SHOW_ERRORS && [ -f "$FIX_LOG" ]; then
  echo ""
  echo "Latest errors:"
  # Extract lines with [ERROR] or [FAILURE], take last 5
  ERROR_LINES=$(grep -E '\[ERROR\]|\[FAILURE\]' "$FIX_LOG" | tail -5)
  if [ -n "$ERROR_LINES" ]; then
    while IFS= read -r line; do
      # Parse timestamp and message
      TS=$(echo "$line" | grep -o '\[[0-9T:-]*\]' | head -1 | tr -d '[]' | sed 's/.*T//' | cut -c1-8)
      MSG=$(echo "$line" | sed 's/\[[^]]*\]//g' | xargs)
      echo "  [${TS}] ${MSG}"
    done <<< "$ERROR_LINES"
  else
    echo "  (no errors)"
  fi
fi
