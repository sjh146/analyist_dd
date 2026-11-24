#!/bin/bash
# analyist_dd DB 일일 백업 — pg_dump | gzip, 7일 보관.
# 실패해도 다음 날 백업을 막지 않는다. 로그: reports/backup_db.log
set -uo pipefail
REPO=/home/jhshi/analyist_dd
DEST="$REPO/data/backups"
mkdir -p "$DEST"
PW=$(grep -m1 '^POSTGRES_PASSWORD=' "$REPO/.env" | cut -d= -f2-)
STAMP=$(date +%Y%m%d)
OUT="$DEST/db_${STAMP}.sql.gz"
LOG="$REPO/reports/backup_db.log"
{
  echo "[$(date '+%F %T')] 시작 $OUT"
  if docker exec -e PGPASSWORD="$PW" stock_postgres pg_dump -U stock_user -d stock_trading --no-owner --no-privileges | gzip -6 > "$OUT"; then
    SIZE=$(du -h "$OUT" | cut -f1)
    echo "[$(date '+%F %T')] 완료 $OUT ($SIZE)"
  else
    echo "[$(date '+%F %T')] 실패 — 부분 파일 삭제"
    rm -f "$OUT"
    exit 1
  fi
  find "$DEST" -name 'db_*.sql.gz' -mtime +7 -print -delete
} >> "$LOG" 2>&1
