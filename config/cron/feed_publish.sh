#!/bin/bash
# analyist_dd 피드 스냅샷 발행 (스크리너 산출물 → data/feed/screener_latest.json)
# trader-agent 가 폴링하는 파일을 갱신한다. 발행만 하므로 몇 초면 끝난다.
set -a; . /home/dduckbeagy/analyist_dd/.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
cd /home/dduckbeagy/analyist_dd || exit 1
exec /usr/bin/python3 scripts/feed_export.py
