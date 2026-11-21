#!/bin/bash
# quarterly_financial_backfill.sh — DART 정기보고서 재무 백필 (월 2회 실행, 분기별 보고서 코드 자동 선택)
#
# WHY (2026-11-21 실측): dart_financial_backfill.py 를 부르는 스케줄이 없어서 3분기 보고서
#   1,181건이 disclosures 에 쌓여 있는데 financial_statements 는 2026-06-30(반기)에서 2개월째
#   멈춰 있었다. 공시 수집(18:10)은 매일 돌지만 재무 파싱은 수동 전용이라 매 분기 같은 공백이 난다.
#
# 동작:
#   - 이번 달로 '지금 제출되고 있는 보고서'를 고른다(제출 시차 1~2개월 반영).
#   - 해당 보고서가 없으면 조용히 종료(월 2회 실행해 제출 지연·재제출을 흡수).
#   - 러너의 진행파일이 이미 끝낸 종목을 건너뛰므로 재실행이 싸다(멱등 upsert).
#
# 사용: quarterly_financial_backfill.sh [--dry-run]
set -uo pipefail

REPO=/home/jhshi/analyist_dd
LOG="$REPO/reports/cron_dart_financial.log"
PY=/usr/bin/python3
BUDGET="${DART_FIN_BUDGET:-900}"
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="--dry-run"

cd "$REPO" || exit 1
ts() { date '+%F %T'; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

if [ -f "$REPO/.env" ]; then set -a; . "$REPO/.env"; set +a; fi
export POSTGRES_HOST="${DART_FIN_PG_HOST:-127.0.0.1}"
export POSTGRES_PORT="${DART_FIN_PG_PORT:-5434}"

month=$(date +%-m)
year=$(date +%Y)
case "$month" in
  3|4)  code=11011; label="사업보고서(연간) previous-year" ;;   # 12-31 결산 → 3~4월 제출
  5|6)  code=11013; label="1분기 보고서" ;;                      # 03-31 결산 → 5월 제출
  8|9)  code=11012; label="반기 보고서" ;;                       # 06-30 결산 → 8월 제출
  11|12) code=11014; label="3분기 보고서" ;;                     # 09-30 결산 → 11월 제출
  *)    log "이번 달($month)은 정기보고서 제출 창이 아니다 — 건너뜀"; exit 0 ;;
esac

fn_year=$year
[ "$code" = "11011" ] && fn_year=$((year - 1))

log "시작 $label (reprt=$code year=$fn_year budget=$BUDGET dry=${DRY:-no})"
# ⚠ 상태파일은 **보고서 코드·연도별로 분리**한다 (2026-11-21 실측): 기본값(data/dart/backfill_state.json)은
#   done/failed 를 한 세트로만 들고 있어서, 8월 반기 백필이 표시한 1,702종목이 11월 3분기 실행에서
#   '완료'로 스킵됐다(그 종목들의 3분기 재무가 영원히 안 들어옴). 러너 docstring 도 코드별 파일을 안내한다.
STATE="data/dart/backfill_state_${code}_${fn_year}.json"
"$PY" scripts/dart_financial_backfill.py --reprt-code "$code" --year "$fn_year" \
      --state-file "$STATE" --max-calls "$BUDGET" $DRY >> "$LOG" 2>&1
rc=$?
log "종료 rc=$rc"
exit 0
