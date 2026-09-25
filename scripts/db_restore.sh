#!/usr/bin/env bash
# db_restore.sh — db_dump.sh 로 만든 덤프를 이 머신의 DB 에 복원한다.
#
#   bash scripts/db_restore.sh dumps/analyist_dd_YYYYMMDD.sql.gz          # 빈 DB 여야 함
#   bash scripts/db_restore.sh --force dumps/....sql.gz                   # 기존 데이터가 있어도 진행
#
# 안전장치
#  - 대상 DB 에 이미 데이터가 있으면 **거부**한다(덮어쓰면 복구 불가). --force 로만 무시.
#  - 복원 후 테이블 수·핵심 테이블 행수를 **대조**해 빈 복원을 잡아낸다.
#  - 스키마만 필요하면 --schema-only.
set -uo pipefail
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJ"

FORCE=0; SCHEMA_ONLY=0; FILE=""
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    --schema-only) SCHEMA_ONLY=1 ;;
    -*) echo "알 수 없는 옵션: $a"; exit 2 ;;
    *) FILE="$a" ;;
  esac
done
[ -n "$FILE" ] || { echo "사용: bash scripts/db_restore.sh [--force] [--schema-only] <덤프.sql.gz|.sql>"; exit 2; }
[ -f "$FILE" ] || { echo "✗ 파일 없음: $FILE"; exit 2; }

[ -f .env ] && { set -a; . ./.env; set +a; }
DB="${POSTGRES_DB:-stock_trading}"
USER="${POSTGRES_USER:-stock_user}"
CONT="${PG_CONTAINER:-stock_postgres}"

docker ps --format '{{.Names}}' | grep -qx "$CONT" || {
  echo "✗ 컨테이너 $CONT 가 떠 있지 않다 — docker compose up -d 먼저"; exit 1; }

# ── 대상 점검 ──
EXIST=$(docker exec "$CONT" psql -U "$USER" -d "$DB" -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'" | tr -d ' ')
ROWS="0"
if [ "${EXIST:-0}" -gt 0 ] 2>/dev/null; then
  ROWS=$(docker exec "$CONT" psql -U "$USER" -d "$DB" -tAc \
    "SELECT COALESCE((SELECT COUNT(*) FROM market_data),0)" 2>/dev/null | tr -d ' ')
fi
echo "대상 DB: 기존 테이블 ${EXIST:-0}개 / market_data ${ROWS:-0}행"
if [ "${ROWS:-0}" -gt 0 ] 2>/dev/null && [ "$FORCE" = 0 ]; then
  echo "✗ 대상에 데이터가 이미 있다. 덮어쓰지 않는다." >&2
  echo "  기존 데이터를 지우고 복원하려면: docker compose down -v && docker compose up -d  (그 뒤 재실행)" >&2
  echo "  또는 정말 덮어쓰려면 --force" >&2
  exit 3
fi

# ── 복원 ──
LOG=/tmp/analyist_dd_restore_$(date +%H%M%S).log
echo "[1/2] 복원 시작 → 로그 $LOG"
if [[ "$FILE" == *.gz ]]; then CAT="zcat"; else CAT="cat"; fi
if [ "$SCHEMA_ONLY" = 1 ]; then
  $CAT "$FILE" | docker exec -i "$CONT" psql -U "$USER" -d "$DB" --schema-only -q > "$LOG" 2>&1
  PY=$?
else
  # ON_ERROR_STOP=0: 대형 덤프에서 사소한 오류(이미 존재하는 롤 등)로 전체가 멈추지 않게 한다.
  $CAT "$FILE" | docker exec -i "$CONT" psql -U "$USER" -d "$DB" -q > "$LOG" 2>&1
  PY=$?
fi
echo "  psql 종료코드: $PY (오류 줄: $(grep -ciE '^ERROR' "$LOG" 2>/dev/null || echo 0)개)"
grep -iE '^ERROR' "$LOG" 2>/dev/null | head -5

# ── 검증 ──
echo "[2/2] 복원 결과 검증"
NEW_T=$(docker exec "$CONT" psql -U "$USER" -d "$DB" -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'" | tr -d ' ')
NEW_R=$(docker exec "$CONT" psql -U "$USER" -d "$DB" -tAc \
  "SELECT COALESCE((SELECT COUNT(*) FROM market_data),0)" 2>/dev/null | tr -d ' ')
echo "  테이블 ${EXIST:-0} → ${NEW_T} / market_data ${ROWS:-0} → ${NEW_R}"
if [ "${NEW_T:-0}" -lt 20 ] 2>/dev/null; then
  echo "✗ 테이블이 ${NEW_T}개뿐 — 복원 실패. $LOG 확인" >&2; exit 1
fi
[ "${NEW_R:-0}" -gt 0 ] 2>/dev/null && echo "  ✓ 데이터 확인" || echo "  ! market_data 가 비어 있다(스키마만 복원됐을 수 있음)"
echo "✓ 복원 완료. 다음: bash scripts/bootstrap_new_machine.sh --check 로 전체 점검"
