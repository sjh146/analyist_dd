#!/bin/bash
# daytrading_feed.sh — 단타 후보 생성 + 피드 발행 (평일 08:35, 개장 전).
#
# WHY(2026-11-17): 피드의 daytrading 후보가 0건이라 트레이더가 단타 경로를 한 번도 쓰지 못했다
# (docs/spec_daytrading_shortstop_2026-11-17.md §2.2b "단타 피드 배선(필수)"). 야간 파이프라인은
# swing/close 만 발행하므로, 개장 전에 daytrading_screener 를 돌려 reports/daytrading_latest.json 을
# 만들고 feed_export 로 data/feed/screener_latest.json 에 합친다.
#
# 실측 함정: daytrading_screener 는 POSTGRES_PORT 기본값이 5432 인데 호스트에는 **5434** 로
# 노출돼 있다(docker port stock_postgres → 0.0.0.0:5434). 기본값 그대로면
# "connection to server at 127.0.0.1:5432 failed" 로 후보 0건이 된다(2026-11-17 실측).
# 그래서 .env 의 비밀번호를 읽어 호스트/포트를 명시한다.
#
# 사용:
#   daytrading_feed.sh            # 실제 발행
#   daytrading_feed.sh --dry-run  # 발행 없이 검증만(feed_export --dry-run)
set -uo pipefail

REPO=/home/jhshi/analyist_dd
LOG="$REPO/reports/cron_daytrading_feed.log"
PY=/usr/bin/python3
SRC="$REPO/reports/daytrading_latest.json"
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="--dry-run"

cd "$REPO" || exit 1
ts() { date '+%F %T'; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

if [ -f "$REPO/.env" ]; then
  set -a; . "$REPO/.env"; set +a
fi
# .env 는 컨테이너 내부 기준(POSTGRES_HOST=postgres, PORT=5432)이므로 **덮어써야** 한다 —
# WSL 호스트에서는 'postgres' 이름이 해석되지 않고(실측 2026-11-17 "could not translate host name"),
# 노출 포트도 5434 다.
export POSTGRES_HOST="${DAYTRADING_PG_HOST:-127.0.0.1}"
export POSTGRES_PORT="${DAYTRADING_PG_PORT:-5434}"

log "start (dry_run=${DRY:-no})"
# 레짐 갱신(2026-11-20 사장 승인): 스크리너 게이트가 '오늘/직전 거래일' asof 만 인정하므로
# 매 발행 전에 지수 상태를 새로 계산한다. 실패해도 기존 동작 유지(fail-open).
"$PY" scripts/market_regime.py >> "$LOG" 2>&1 || log "WARN market_regime 실패 — 기존 상태로 진행"
# 2026-11-23 저녁: risk_off 에서도 후보 생성(위험은 트레이더가 조인다).
REGIME_ARG="--regime-block ${DAYTRADING_REGIME_BLOCK:-off}"
"$PY" scripts/daytrading_screener.py --top-n 20 --json-out "$SRC" $REGIME_ARG >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ] || [ ! -f "$SRC" ]; then
  log "FAIL screener rc=$rc — 발행 중단(기존 피드 유지)"
  exit 1
fi

n=$("$PY" - "$SRC" <<'EOF'
import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    print(0); raise SystemExit
items=d.get("items") if isinstance(d,dict) else d
print(len(items or []))
EOF
)
log "screener ok items=$n"
if [ "$n" -lt 1 ]; then
  log "WARN 단타 후보 0건 — 발행은 하되 daytrading 은 빈 목록이 된다"
fi

"$PY" scripts/feed_export.py --daytrading "$SRC" $DRY >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  log "FAIL feed_export rc=$rc"
  exit 1
fi

if [ -z "$DRY" ]; then
  cnt=$("$PY" - <<'EOF'
import json
try:
    d=json.load(open('/home/jhshi/analyist_dd/data/feed/screener_latest.json'))
    print(len((d.get('candidates',{}).get('daytrading',{}) or {}).get('items') or []))
except Exception:
    print('err')
EOF
)
  log "published daytrading_items=$cnt generated_at=$("$PY" -c "import json;print(json.load(open('/home/jhshi/analyist_dd/data/feed/screener_latest.json')).get('generated_at'))" 2>/dev/null)"
fi
log "done"
exit 0
