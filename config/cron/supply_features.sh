#!/bin/bash
# 수급·시장 피처 빌더 (R10) — 파이프라인에 호출이 없어 수동 실행 때만 갱신되던 경로.
#   2026-11-13 배선: kis_supply(16:20)/kis_short_program(19:30)/krx_offline(19:40) 이후 20:15 에 돌린다.
#   창은 최근 45일 — 빌더의 DELETE 가 이제 창(since 이후)만 지우므로 과거 이력은 보존된다.
set -u
cd /home/jhshi/analyist_dd || exit 1
set -a; . ./.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
SINCE="$(date -d '45 days ago' +%F)"
LOG=/home/jhshi/analyist_dd/reports/cron_supply_features.log
echo "[$(date '+%F %T')] build_supply_market_features since=$SINCE" >> "$LOG"
timeout 1800 python3 scripts/build_supply_market_features.py --since "$SINCE" >> "$LOG" 2>&1
echo "[$(date '+%F %T')] done rc=$?" >> "$LOG"
exit 0
