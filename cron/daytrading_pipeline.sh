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
# 최소 거래대금(원). HI 는 실측 근거(왕복비용 0.4% 이하 종목이 대부분 10억 이상),
# 09:10 개장 직후 0건이면 LO 로 자동 강등된다.
# 2026-11-23 저녁 사용자 지시("내일부터 매매할 수 있게"): risk_off 에서도 후보를 만든다.
# 위험 관리는 트레이더가 맡는다(risk_off 기본 보수: 사이즈 ×0.5·엣지 +2·일 2건).
# 레짐 게이트를 다시 켜려면 DAYTRADING_REGIME_BLOCK=on 으로 실행한다.
REGIME_ARG="--regime-block ${DAYTRADING_REGIME_BLOCK:-off}"
MIN_VALUE_HI="${DAYTRADING_MIN_VALUE_HI:-1000000000}"
MIN_VALUE_LO="${DAYTRADING_MIN_VALUE_LO:-5000000000}"

cd "$REPO" || exit 1
ts() { date '+%F %T'; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

if [ -f "$REPO/.env" ]; then set -a; . "$REPO/.env"; set +a; fi
export POSTGRES_HOST="${DAYTRADING_PG_HOST:-127.0.0.1}"
export POSTGRES_PORT="${DAYTRADING_PG_PORT:-5434}"
export BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8100}"

log "start (dry_run=${DRY:-no})"

OPEN_GATE="${DAYTRADING_OPEN_GATE_PCT:-0}"
if [ "${OPEN_GATE:-0}" != "0" ]; then
  if "$PY" "$REPO/scripts/kosdaq_open_gate.py" --threshold "$OPEN_GATE" \
       --json-out "$REPO/data/state/kosdaq_open_gate.json" >> "$LOG" 2>&1; then
    log "개장 게이트 통과(코스닥 >= +${OPEN_GATE}%) — 단타 후보 생성"
    REGIME_ARG="--regime-block off"
  else
    log "개장 게이트 미달/판정불가 — 단타 차단(--regime-block on)"
    REGIME_ARG="--regime-block on"
  fi
fi

# 2026-11-23: 레짐 게이트 입력이 컨테이너에 안 보이면 게이트가 **조용히** 무력화된다
# (실측: 같은 날 호스트 실행은 risk_off 로 차단, 컨테이너 실행은 20건 통과 —
#  data/state 가 마운트되지 않아 파일이 없고 _load_regime 이 None → fail-open).
# 조용한 불일치를 막기 위해 매 실행마다 가시성을 확인하고 로그에 남긴다.
if ! docker exec "$CONTAINER" test -f /app/data/state/market_regime.json 2>/dev/null; then
  log "WARN 컨테이너에 /app/data/state/market_regime.json 없음 — 레짐 게이트가 무력화(fail-open)됩니다. docker-compose xgboost-ml 의 ./data/state 마운트를 확인하세요"
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

# 1) 상승률 랭킹 → 유니버스 파일 (브리지 없으면 fail-open 으로 전체 유니버스)
"$PY" scripts/rank_universe.py --pct-from 2 --pct-to 12 --pages 7 --quiet >> "$LOG" 2>&1 \
  || log "WARN rank_universe 실패 — 전체 유니버스로 진행(fail-open)"

run_screener() {  # $1 = 최소 거래대금, $2 = 추가 인자(대역 파일), $3 = 라벨
  local mv="${1:-$MIN_VALUE_LO}" extra="${2:-}" label="${3:-band}"
  # 레짐 게이트는 REGIME_ARG 로만 제어한다(기본 off = 후보 생성 허용).
  docker exec -w /app -e PYTHONPATH=/app "$CONTAINER" \
    python scripts/daytrading_screener.py --top-n 20 --json-out "$CONT_JSON" \
    --min-trading-value "$mv" $extra $REGIME_ARG >> "$LOG" 2>&1
  local rc=$?
  local n
  n=$(count_items "$CONT_HOST")
  log "screener($label) rc=$rc items=$n"
  echo "$n"
}

# 2026-11-27: 기본 하한을 50억으로 되돌린다(사용자 지시 '유동성 높은 종목').
# 기존에는 첫 시도가 10억이라 50억 정책이 사실상 적용되지 않았고, 실제로 27~45억 종목이
# 후보로 발행돼 매수까지 이어졌다(117730). 50억에서 0건일 때만 10억으로 완화한다.
n=$(run_screener "$MIN_VALUE_LO" "--rank-band-file $UNIV" "band-50eok")
if [ "${n:-0}" -eq 0 ]; then
  log "WARN 50억 하한에서 후보 0건 — 10억으로 완화 재시도"
  n=$(run_screener "$MIN_VALUE_HI" "--rank-band-file $UNIV" "band-10eok")
fi
if [ "${n:-0}" -eq 0 ]; then
  log "WARN 대역 적용 후 후보 0건 — 대역 없이 재시도"
  n=$(run_screener "$MIN_VALUE_LO" "" "fallback")
fi
if [ "${n:-0}" -eq 0 ]; then
  if printf '%s' "$REGIME_ARG" | grep -q ' on$'; then
    log "WARN 후보 0건 + 게이트 차단 — 피드의 단타 목록을 비워 stale 진입을 막는다"
    "$PY" scripts/feed_clear_daytrading.py $DRY >> "$LOG" 2>&1 || log "WARN clear 실패"
chmod 644 data/feed/screener_latest.json 2>/dev/null
    exit 0
  fi
  log "FAIL 후보 0건 — 발행 중단(기존 피드 유지)"
  exit 1
fi

cp -f "$CONT_HOST" "$SRC" || { log "FAIL 원천 복사 실패"; exit 1; }
log "원천 갱신: $SRC (items=$n)"

# 3) 발행
chmod 644 data/feed/screener_latest.json 2>/dev/null; "$PY" scripts/feed_export.py --daytrading "$SRC" $DRY >> "$LOG" 2>&1
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
