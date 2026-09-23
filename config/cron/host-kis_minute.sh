#!/bin/bash
# analyist_dd 분봉 수집 (KIS) — 우선순위 유니버스만.
#
# 왜 전 종목이 아닌가 (2026-09-23 실측):
#   KIS 당일분봉 API는 fid_cnt를 줘도 페이지당 30행만 준다 → 하루 391분 = 14페이지.
#   전 종목(2,770) × 14콜 ≈ 38,780콜 × 약 3.7초 ≈ 40시간 → 야간 1회로 불가능.
#   300종목 × 14콜 ≈ 4,200콜 ≈ 4시간이 현실적 상한(23:00 시작 → 약 03:00 종료).
#   유니버스 = 최근 20거래일 평균 거래대금 상위 300 (KOSPI 159 / KOSDAQ 141).
#   유니버스를 갱신하려면 같은 기준으로 data/minute_universe.json 을 다시 만든다.
#   ※ 이 API는 당일 데이터만 준다 — 크론이 그날 안 돌면 그날 분봉은 복구 불가.
#
# 되돌리기: /home/jhshi/cron/kis_minute.sh.bak-20260923 (전 종목·10페이지 기본값)
set -a; . /home/dduckbeagy/analyist_dd/.env; set +a
# 호스트 프로세스는 컨테이너 네트워크 밖 → 호스트 매핑 포트로 접속
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
# 페이지당 30행 고정 → 정규장 391분 전체를 받으려면 14페이지가 필요(기본값 10은 개장 후 91분 누락)
export KIS_MINUTE_MAX_PAGES="${KIS_MINUTE_MAX_PAGES:-14}"
cd /home/dduckbeagy/analyist_dd || exit 1
cd services/kis-collector && exec /usr/bin/python3 -m kis_app.main --job minute \
    --date "$(date +%Y%m%d)" \
    --universe-file /home/dduckbeagy/analyist_dd/data/minute_universe.json
