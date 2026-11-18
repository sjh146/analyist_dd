#!/bin/bash
# daytrading_feed_intraday.sh — 장중 단타 후보 재발행 (크레온 상승률 랭킹으로 유니버스 압축)
#
# WHY (2026-11-18 사용자 선택 1번, docs/spec_rank_universe.md):
#   단타스크리너는 KOSDAQ 전체(20거래일 이상)를 점수화하므로, 크레온 당일 상승률 랭킹
#   상위권(기본 2~12%)과 교집합을 취해 "오늘 실제로 움직이는 종목"으로 유니버스를 줄인다.
#   매수 판단 규칙(칼만·모델·거래량·변동성·엣지 게이트)은 그대로 — 유니버스만 압축한다.
#
# 동작:
#   1) rank_universe.py  → data/state/rank_universe.json (실패해도 계속: fail-open)
#   2) daytrading_screener.py --universe-file ... (후보 0건이면 제한 없이 1회 재시도)
#   3) feed_export.py --daytrading ... 로 발행 (08:35 크론과 동일 경로)
#   후보가 끝내 0건이면 **발행하지 않는다**(기존 피드 유지 — 빈 피드로 매매를 멈추지 않게).
#
# 사용: daytrading_feed_intraday.sh [--dry-run]
set -uo pipefail

REPO=/home/jhshi/analyist_dd
LOG="$REPO/reports/cron_daytrading_intraday.log"
PY=/usr/bin/python3
SRC="$REPO/reports/daytrading_latest.json"
UNIV="$REPO/data/state/rank_universe.json"
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="--dry-run"

cd "$REPO" || exit 1
ts() { date '+%F %T'; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# .env 는 컨테이너 내부 기준(POSTGRES_HOST=postgres, 5432)이므로 호스트 값으로 덮어쓴다.
if [ -f "$REPO/.env" ]; then
  set -a; . "$REPO/.env"; set +a
fi
export POSTGRES_HOST="${DAYTRADING_PG_HOST:-127.0.0.1}"
export POSTGRES_PORT="${DAYTRADING_PG_PORT:-5434}"
export BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8100}"

log "start (dry_run=${DRY:-no})"

# 1) 상승률 랭킹 → 유니버스 파일 (실패해도 체인 유지)
"$PY" scripts/rank_universe.py --pct-from 2 --pct-to 12 --pages 7 --quiet >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  log "WARN rank_universe rc=$rc — 전체 유니버스로 진행(fail-open)"
fi

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

# 2) 유니버스 압축 스크리너 → 후보 0건이면 제한 없이 1회 재시도
"$PY" scripts/daytrading_screener.py --top-n 20 --universe-file "$UNIV" --json-out "$SRC" >> "$LOG" 2>&1
n=$(count_items "$SRC")
log "screener(restricted) items=$n"
if [ "$n" -eq 0 ]; then
  log "WARN 압축 유니버스에서 후보 0건 — 전체 유니버스로 재시도"
  "$PY" scripts/daytrading_screener.py --top-n 20 --json-out "$SRC" >> "$LOG" 2>&1
  n=$(count_items "$SRC")
  log "screener(fallback) items=$n"
fi
if [ "$n" -eq 0 ]; then
  log "FAIL 후보 0건 — 발행 중단(기존 피드 유지)"
  exit 1
fi

# 3) 발행
"$PY" scripts/feed_export.py --daytrading "$SRC" $DRY >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
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
  log "published daytrading_items=$cnt generated_at=$("$PY" -c "import json;print(json.load(open('$REPO/data/feed/screener_latest.json')).get('generated_at'))" 2>/dev/null)"
fi
log "done"
exit 0
