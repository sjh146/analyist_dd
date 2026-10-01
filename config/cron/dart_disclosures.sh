#!/bin/bash
# analyist_dd 크론 래퍼 — DART 공시 인덱스(disclosures) 백필
#
# 왜 필요한가 (R25, 2026-10-01 실측):
#   disclosures 는 재무 피처의 '공시 지연·기간 구분'을 가정값(90/45일) 대신 실제 접수일로
#   계산하는 원천인데, 이를 채우는 경로가 수동 백필 하나뿐이었다 → 2026-09-23 이후 정지
#   (212,861행 고정). 서비스 내 DartCollector 는 import 하는 코드가 레포에 0곳(미배선).
#
# 사용:
#   dart_disclosures.sh              평일 — 최근 2개월 창, 유형 B,D,E,I (신규 공시 추적)
#   dart_disclosures.sh --regular    주 1회 — 정기공시(A) 3개월 창 (반기/분기 시즌 대비)
#
# 왜 두 갈래인가 (2026-10-01 실측):
#   2개월 창 × A,B,D,E,I 는 페이지 루프 때문에 420초를 넘겨 크론으로 부적합했다(콜 ~150).
#   하루 1회는 B,D,E,I·2개월(콜 ~25, 1~2분)로 좁히고, 페이지가 큰 A(정기공시)는 주 1회로 분리한다.
#   창이 겹쳐도 upsert 라 중복이 생기지 않는다(재실행 시 신규 0행·rc=0).
set -a; . /home/dduckbeagy/analyist_dd/.env; set +a
# 호스트 프로세스는 컨테이너 네트워크 밖 → 호스트 매핑 포트로 접속
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="${POSTGRES_HOST_PORT:-5434}"
export PROJ_DIR=/home/dduckbeagy/analyist_dd
cd /home/dduckbeagy/analyist_dd || exit 1

if [ "${1:-}" = "--regular" ]; then
    MODE="regular"; TYPES="A,B,D,E,I"; SINCE="$(date -d '-3 month' +%F)"
    DESC="3개월 창(누락 안전망)"
else
    MODE="daily"; TYPES="B,D,E,I"; SINCE="$(date +%Y-%m-01)"
    DESC="이번 달 창"
fi

echo "[$(date '+%F %T')] DART 공시 백필 시작 (mode=$MODE, $DESC, 유형 $TYPES)"
exec /usr/bin/python3 scripts/dart_disclosure_backfill.py \
    --since "$SINCE" \
    --types "$TYPES" \
    --max-calls 200
