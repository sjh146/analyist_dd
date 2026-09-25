#!/usr/bin/env bash
# db_dump.sh — 운영 DB 를 압축 덤프로 내보낸다(다른 머신 이식용).
#
#   bash scripts/db_dump.sh                 # dumps/analyist_dd_<시각>.sql.gz
#   bash scripts/db_dump.sh /path/out.sql.gz
#
# 왜 필요한가: 새 머신의 DB 는 **비어 있다**. init-scripts 는 스키마만 만들고 데이터는 넣지 않는다.
# 수집기를 처음부터 다시 돌리면 수 시간~수일이 걸리므로, 기존 머신의 덤프를 옮기는 편이 빠르다.
#
# 검증: gzip 무결성 + 덤프 안의 CREATE TABLE 수 vs 실제 DB 테이블 수를 대조해 "빈 덤프"를 잡아낸다.
set -uo pipefail
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJ"

[ -f .env ] && { set -a; . ./.env; set +a; }
DB="${POSTGRES_DB:-stock_trading}"
USER="${POSTGRES_USER:-stock_user}"
CONT="${PG_CONTAINER:-stock_postgres}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONT"; then
  echo "✗ 컨테이너 $CONT 가 떠 있지 않다 — docker compose up -d 먼저" >&2
  exit 1
fi

OUT="${1:-$PROJ/dumps/analyist_dd_$(date +%Y%m%d-%H%M%S).sql.gz}"
mkdir -p "$(dirname "$OUT")"

echo "[1/3] pg_dump → $OUT"
if ! docker exec "$CONT" pg_dump -U "$USER" -d "$DB" --no-owner --no-privileges | gzip > "$OUT"; then
  echo "✗ pg_dump 실패" >&2; rm -f "$OUT"; exit 1
fi

echo "[2/3] gzip 무결성"
gzip -t "$OUT" && echo "  ✓ 압축 무결성 통과" || { echo "✗ 손상된 압축" >&2; exit 1; }

echo "[3/3] 내용 검증 (빈 덤프 방지)"
TABLES=$(zcat "$OUT" | grep -c '^CREATE TABLE')
DB_TABLES=$(docker exec "$CONT" psql -U "$USER" -d "$DB" -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'" | tr -d ' ')
SIZE=$(du -h "$OUT" | cut -f1)
echo "  덤프 내 CREATE TABLE: $TABLES / DB 실제 테이블: $DB_TABLES / 크기: $SIZE"
if [ "$TABLES" -lt 20 ] || [ "$DB_TABLES" -lt 20 ]; then
  echo "✗ 테이블 수가 비정상(<20) — 빈 덤프일 가능성" >&2; exit 1
fi
for t in market_data financial_statements foreign_institutional; do
  n=$(zcat "$OUT" | grep -c "^COPY public.$t " || true)
  echo "  $t COPY 섹션: $n"
done
echo "✓ 완료: $OUT"
echo "  새 머신으로 복사 후: bash scripts/db_restore.sh $OUT"
