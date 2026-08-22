#!/bin/bash
# trade-executor mock 실행 루프 런처 (paper trading)
# - USE_MOCK_CREON=true → MockCreonExecutor (Creon API 불필요)
# - 로컬 Redis(6379) / stock_postgres(5434) 연결
# - 장중 시간 무관 처리 (mock 검증용 — TRADING 0~23시)
set -a
source /home/dduckbeagy/analyist_dd/.env
set +a

export USE_MOCK_CREON=true
# config.py는 REDIS_HOST가 아니라 BRIDGE_HOST/BRIDGE_PORT를 읽음 (Windows VM 브리지 설계)
export BRIDGE_HOST=127.0.0.1
export BRIDGE_PORT="${REDIS_PORT:-6379}"
export REDIS_HOST=127.0.0.1
export REDIS_PORT="${REDIS_PORT:-6379}"
export PG_HOST=127.0.0.1
export PG_PORT="${POSTGRES_HOST_PORT:-5434}"
export PG_DB="$POSTGRES_DB"
export PG_USER="$POSTGRES_USER"
export PG_PASSWORD="$POSTGRES_PASSWORD"
export TRADING_START_HOUR=0
export TRADING_END_HOUR=23
export PYTHONPATH="/home/dduckbeagy/analyist_dd:/home/dduckbeagy/analyist_dd/services/trade-executor"

cd /home/dduckbeagy/analyist_dd/services/trade-executor
exec /home/dduckbeagy/analyist_dd/.venv-te/bin/python main.py
