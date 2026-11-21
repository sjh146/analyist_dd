#!/bin/bash
# daytrading_pipeline.sh — 단타(daytrading) 후보 생성 + 발행 (평일 3회)
#
# WHY (R31 승인 2026-11-21): daytrading 피드 원천 reports/daytrading_latest.json 을 만드는
# 스케줄이 없어서 발행본 daytrading 섹션이 items: [] 로 나갔다(실측: 후보 20건 생성은 되는데
# 생성 주체가 사람 수동 실행뿐). 이 래퍼가 생성→발행을 상시 경로로 만든다.
#
# 순서:
#   1) rank_universe.py  → data/state/rank_universe.json  (크레온 상승률 랭킹, 실패해도 계속)
#   2) 단타 스크리너를 **컨테이너 안에서** 실행 — 호스트 실행은 챔피언 모델을 못 읽어
#      '모델미가용'(칼만·거래량만)으로 후보를 낸다. 컨테이너 경로는 모델 예측이 붙는다.
#      출력은 /app/data/reports(= services/xgboost-ml/data/reports) → 호스트 사본으로 복사.
#   3) 후보 0건이면 대역 없이 1회 재시도, 그래도 0건이면 발행하지 않는다(기존 피드 유지).
#   4) feed_export.py --daytrading 으로 발행.
#
# 사용: daytrading_pipeline.sh [--dry-run]
set -uo pipefail

REPO=/home/jhshi/analyist_dd
LOG="$REPO/reports/cron_daytrading_pipeline.log"
PY=/usr/bin/python3
UNIV="$REPO/data/state/rank_universe.json"
SRC="$REPO/reports/daytrading_latest.json"
CONT_JSON=/app/data/reports/daytrading_latest.json
CONT_HOST="$REPO/services/xgboost-ml/data/reports/daytrading_latest.json"
CONTAINER=stock_xgboost_ml
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="--dry-run"

cd "$REPO" || exit 1
ts() { date '+%F %T'; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

if [ -f "$REPO/.env" ]; then set -a; . "$REPO/.env"; set +a; fi
export POSTGRES_HOST="${DAYTRADING_PG_HOST:-127.0.0.1}"
export POSTGRES_PORT="${DAYTRADING_PG_PORT:-5434}"
export BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8100}"

log "start (dry_run=${DRY:-no})"

count_items() {
  "$PY" - "$1" <<'EOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print(0); raise SystemExit
items = d.get("items") if isinstance(d, dict) else d
print(len(items or []))
EOF
}

# 1) 상승률 랭킹 → 유니버스 파일 (브리지 없으면 fail-open 으로 전체 유니버스)
"$PY" scripts/rank_universe.py --pct-from 2 --pct-to 12 --pages 7 --quiet >> "$LOG" 2>&1 \
  || log "WARN rank_universe 실패 — 전체 유니버스로 진행(fail-open)"

run_screener() {  # $1 = 추가 인자(대역 파일), $2 = 라벨
  local extra="${1:-}" label="${2:-band}"
  docker exec -w /app -e PYTHONPATH=/app "$CONTAINER" \
    python scripts/daytrading_screener.py --top-n 20 --json-out "$CONT_JSON" $extra >> "$LOG" 2>&1
  local rc=$?
  local n
  n=$(count_items "$CONT_HOST")
  log "screener($label) rc=$rc items=$n"
  echo "$n"
}

n=$(run_screener "--rank-band-file $UNIV" "rank-band")
if [ "${n:-0}" -eq 0 ]; then
  log "WARN 대역 적용 후 후보 0건 — 대역 없이 재시도"
  n=$(run_screener "" "fallback")
fi
if [ "${n:-0}" -eq 0 ]; then
  log "FAIL 후보 0건 — 발행 중단(기존 피드 유지)"
  exit 1
fi

cp -f "$CONT_HOST" "$SRC" || { log "FAIL 원천 복사 실패"; exit 1; }
log "원천 갱신: $SRC (items=$n)"

# 3) 발행
"$PY" scripts/feed_export.py --daytrading "$SRC" $DRY >> "$LOG" 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
  log "FAIL feed_export rc=$rc — 기존 피드 유지"
  exit 1
fi

if [ -z "$DRY" ]; then
  cnt=$("$PY" - <<'EOF'
import json
try:
    d = json.load(open('/home/jhshi/analyist_dd/data/feed/screener_latest.json'))
    print(len((d.get('candidates', {}).get('daytrading', {}) or {}).get('items') or []))
except Exception:
    print('err')
EOF
)
  log "published daytrading_items=$cnt"
fi
log "done"
exit 0
