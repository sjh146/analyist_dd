#!/bin/bash
# 종토방 SNS 수집 (NaverBoardCollector → sns_posts). 컨테이너 안에서 실행해야 app 패키지가 잡힌다.
#   2026-11-13 배선: 러너는 있었지만 스케줄이 없어 sns_posts 가 11-11 이후 0건이었다.
#   ON CONFLICT DO NOTHING 이라 재실행에 안전하다. 딜레이는 수집기 내장(0.7s).
set -u
LOG=/home/jhshi/analyist_dd/reports/cron_sns_collect.log
LIMIT="${SNS_LIMIT:-250}"
PAGES="${SNS_MAX_PAGES:-2}"
echo "[$(date '+%F %T')] sns collect start (limit=$LIMIT pages=$PAGES)" >> "$LOG"
timeout 900 docker exec -w /app stock_news_analyzer \
    python -u scripts/sns_collect_runner.py --limit "$LIMIT" --max-pages "$PAGES" \
    >> "$LOG" 2>&1
rc=$?
echo "[$(date '+%F %T')] sns collect done rc=$rc" >> "$LOG"
exit 0
