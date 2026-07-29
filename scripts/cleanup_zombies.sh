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

SIX_HOURS=21600

mkdir -p "$REPORT_DIR"

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cleanup started"

  killed=0
  remaining=0
  declare -A killed_pids

  for script in "${SCRIPT_NAMES[@]}"; do
    mapfile -t pids < <(
      ps -e -o pid,etimes,args --no-headers 2>/dev/null \
        | grep -F "$script" \
        | grep -v grep \
        | grep -v "cleanup_zombies" \
        | awk '{print $1}' \
        || true
    )

    if [[ ${#pids[@]} -eq 0 ]]; then
      continue
    fi

    declare -A etime_map
    for pid in "${pids[@]}"; do
      etime_map["$pid"]=$(ps -o etimes= -p "$pid" 2>/dev/null || echo "0")
    done

    if [[ ${#pids[@]} -ge 2 ]]; then
      sorted=()
      while IFS= read -r pid; do
        sorted+=("$pid")
      done < <(
        for pid in "${pids[@]}"; do
          echo "$pid ${etime_map[$pid]}"
        done | sort -k2 -n | awk '{print $1}'
      )

      keep="${sorted[0]}"
      for ((i = 1; i < ${#sorted[@]}; i++)); do
        pid="${sorted[$i]}"
        if kill -0 "$pid" 2>/dev/null; then
          echo "Killing duplicate $script (PID $pid, elapsed ${etime_map[$pid]}s)"
          if ! kill "$pid" 2>/dev/null; then
            echo "ERROR: Failed to kill PID $pid"
            exit 1
          fi
          killed_pids["$pid"]=1
          ((killed++))
        fi
      done
    fi

    for pid in "${pids[@]}"; do
      if [[ -n "${killed_pids[$pid]:-}" ]]; then
        continue
      fi
      if kill -0 "$pid" 2>/dev/null; then
        et=${etime_map[$pid]:-0}
        if [[ $et -ge $SIX_HOURS ]]; then
          echo "Killing old $script (PID $pid, ${et}s > 6h)"
          if ! kill "$pid" 2>/dev/null; then
            echo "ERROR: Failed to kill PID $pid"
            exit 1
          fi
          killed_pids["$pid"]=1
          ((killed++))
        else
          ((remaining++))
        fi
      fi
    done

    unset etime_map
  done

  echo "Killed ${killed} zombie processes, ${remaining} remaining"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cleanup finished"

} | tee "$LOG_FILE"

exit 0
