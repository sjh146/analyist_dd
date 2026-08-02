#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT_DIR="$PROJECT_ROOT/reports"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="$REPORT_DIR/cleanup_zombies_${TIMESTAMP}.log"

SCRIPT_NAMES=(
  "full_pipeline_dd.sh"
  "auto_pipeline_loop.sh"
  "auto_train_loop.sh"
  "ml_infinite_loop.sh"
)

mkdir -p "$REPORT_DIR"

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cleanup started"

  killed=0
  remaining=0

  for script in "${SCRIPT_NAMES[@]}"; do
    mapfile -t pids < <(
      ps -e -o pid,etimes,args --no-headers 2>/dev/null \
        | awk -v s="$script" '
            $0 ~ /grep/ { next }
            $0 ~ /cleanup_zombies/ { next }
            $3 ~ /(^|\/)(tee|tail|grep)$/ { next }
            $0 ~ /bash -c/ { next }
            index($0, "bash scripts/") == 0 { next }
            index($0, s) == 0 { next }
            { print $1 }
          ' \
        || true
    )

    if [[ ${#pids[@]} -eq 0 ]]; then
      continue
    fi

    declare -A etime_map
    for pid in "${pids[@]}"; do
      etime_map["$pid"]=$(ps -o etimes= -p "$pid" 2>/dev/null || echo "0")
    done

    if [[ ${#pids[@]} -eq 1 ]]; then
      pid="${pids[0]}"
      if kill -0 "$pid" 2>/dev/null; then
        echo "Single instance of $script (PID $pid, ${etime_map[$pid]}s) left untouched"
        remaining=$((remaining + 1))
      fi
      unset etime_map
      continue
    fi

    sorted=()
    while IFS= read -r pid; do
      sorted+=("$pid")
    done < <(
      for pid in "${pids[@]}"; do
        echo "$pid ${etime_map[$pid]}"
      done | sort -k2 -n | awk '{print $1}'
    )

    keep="${sorted[0]}"
    if kill -0 "$keep" 2>/dev/null; then
      echo "Keeping newest $script (PID $keep, ${etime_map[$keep]}s)"
      ((remaining++))
    fi

    for ((i = 1; i < ${#sorted[@]}; i++)); do
      pid="${sorted[$i]}"
      if kill -0 "$pid" 2>/dev/null; then
        echo "Killing duplicate $script (PID $pid, elapsed ${etime_map[$pid]}s)"
        if ! kill "$pid" 2>/dev/null; then
          echo "ERROR: Failed to kill PID $pid"
          exit 1
        fi
        ((killed++))
      fi
    done

    unset etime_map
  done

  echo "Killed ${killed} duplicate process(es), ${remaining} remaining"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cleanup finished"

} | tee "$LOG_FILE"

exit 0
