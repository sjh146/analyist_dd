#!/bin/bash
# 종가 스크리너 잡 + 피드 발행 (장 마감 전 실행 — 진입창 14:50~15:25)
# job-runner 컨테이너의 잡 스크립트가 reports/close_latest.json 을 만들고,
# 이어서 피드 스냅샷을 갱신한다.
set -a; . /home/dduckbeagy/analyist_dd/.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
cd /home/dduckbeagy/analyist_dd || exit 1

echo "[$(date '+%F %T')] 종가 스크리너 잡 시작"
docker exec stock_job_runner python /app/app/scripts/run_close_job.py
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "[$(date '+%F %T')] 종가 잡 실패(exit=$rc) — 피드 발행 생략"
    exit "$rc"
fi
echo "[$(date '+%F %T')] 피드 발행"
exec /usr/bin/python3 scripts/feed_export.py
