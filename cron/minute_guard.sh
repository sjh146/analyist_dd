#!/bin/bash
# 분봉 누락 자동 복구 — KIS 수집이 실패한 날 네이버 백필로 메운다.
# 근거(2026-11-24): 11-23 23:16 KIS 크론이 자격증명 거부로 300종목 전부 실패했고,
# 분봉은 당일분만 API로 받을 수 있어 다음 날이면 복구 불가였다. 저녁에 자동으로 확인한다.
set -uo pipefail
REPO=/home/jhshi/analyist_dd
LOG="$REPO/reports/minute_guard.log"
DAY="${1:-$(date +%Y-%m-%d)}"
CODES="$REPO/data/minute_universe.json"

set -a
. "$REPO/.env"
set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434

COUNT=$(docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" stock_postgres psql -U "${POSTGRES_USER:-stock_user}" -d "${POSTGRES_DB:-stock_trading}" -t -A -c "select coalesce(count(distinct stock_code),0) from minute_close_bars where trade_date='$DAY';" 2>/dev/null | tr -d ' ')

echo "[$(date '+%F %T')] $DAY 분봉 종목수=${COUNT:-0}" >> "$LOG"

if [ -z "${COUNT:-}" ]; then
  echo "[$(date '+%F %T')] DB 조회 실패 — 건너뜀" >> "$LOG"
  exit 1
fi
if [ "$COUNT" -ge 150 ]; then
  echo "[$(date '+%F %T')] 충분 — 복구 불필요" >> "$LOG"
  exit 0
fi

python3 - "$CODES" <<'PY' > /tmp/minute_guard_codes.txt
import json, sys
d = json.load(open(sys.argv[1]))
codes = list(d.keys()) if isinstance(d, dict) else [x.get("code") for x in d]
print("\n".join([c for c in codes if c]))
PY

cd "$REPO" || exit 1
python3 scripts/naver_minute_backfill.py --from "$DAY" --to "$DAY" \
  --codes-file /tmp/minute_guard_codes.txt --sleep 0.2 >> "$LOG" 2>&1
RC=$?
echo "[$(date '+%F %T')] 백필 rc=$RC" >> "$LOG"
exit $RC
