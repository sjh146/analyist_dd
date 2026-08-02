#!/usr/bin/env bash
# cleanup_news_data.sh — Delete news_analysis / stock_sentiment rows older than 2 days.
# Derived/analyzable news data only. Core financial tables (trade_orders, positions,
# market_data, ml_predictions, ...) are NEVER touched.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT_DIR="$PROJECT_ROOT/reports"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="$REPORT_DIR/news_cleanup_${TIMESTAMP}.log"

CONTAINER="stock_postgres"
DB_USER="stock_user"
DB_NAME="stock_trading"
RETENTION_DAYS=2
CMD_TIMEOUT=300

mkdir -p "$REPORT_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

die() {
  log "ERROR: $*" >&2
  exit 1
}

# run_psql — psql inside the container, bounded by timeout so a locked table
# fails cleanly instead of hanging the cron job forever.
run_psql() {
  timeout "$CMD_TIMEOUT" docker exec "$CONTAINER" \
    psql -U "$DB_USER" -d "$DB_NAME" -t -A -c "$1" 2>/dev/null
}

{
  log "Cleanup started (retention: ${RETENTION_DAYS} days)"
  log "Container: ${CONTAINER}, db: ${DB_NAME}, user: ${DB_USER}"

  docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" \
    || die "Container ${CONTAINER} is not running"

  for table in news_analysis stock_sentiment; do
    exists=$(run_psql "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='${table}';") \
      || die "Failed to query pg_tables for ${table}"
    [[ "$exists" == "1" ]] || die "Table ${table} not found in ${DB_NAME}"
  done

  news_before=$(run_psql "SELECT COUNT(*) FROM news_analysis;") \
    || die "Failed to count news_analysis"
  sent_before=$(run_psql "SELECT COUNT(*) FROM stock_sentiment;") \
    || die "Failed to count stock_sentiment"

  log "Before: news_analysis=${news_before}, stock_sentiment=${sent_before}"

  news_deleted=$(run_psql "DELETE FROM news_analysis WHERE published_at < NOW() - INTERVAL '${RETENTION_DAYS} days';") \
    || die "DELETE from news_analysis failed"
  sent_deleted=$(run_psql "DELETE FROM stock_sentiment WHERE analysis_date < NOW() - INTERVAL '${RETENTION_DAYS} days';") \
    || die "DELETE from stock_sentiment failed"

  news_after=$(run_psql "SELECT COUNT(*) FROM news_analysis;") \
    || die "Failed to count news_analysis after delete"
  sent_after=$(run_psql "SELECT COUNT(*) FROM stock_sentiment;") \
    || die "Failed to count stock_sentiment after delete"

  log "Deleted: news_analysis=${news_deleted}, stock_sentiment=${sent_deleted}"
  log "After: news_analysis=${news_after}, stock_sentiment=${sent_after}"
  log "Cleanup finished OK"
} | tee "$LOG_FILE"

exit 0
