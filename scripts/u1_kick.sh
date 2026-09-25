#!/usr/bin/env bash
# ~/.hermes/scripts/u1_kick.sh — U1(150종목 패널 빌드)을 부하가 내려가면 시작시킨다.
#
# WHY: 백그라운드 런처가 SIGTERM 으로 죽어 U1 이 시작되지 않았다(실측 2026-09-25 21:0x).
#      세션과 무관하게 살아남아야 하므로 **크론**(15분 주기, no_agent)으로 돌린다.
#      이미 실행 중이면 즉시 침묵 종료(중복 방지), 부하가 낮거나 마감(22:30)을 넘기면 시작한다.
#      시작할 때만 출력한다 → 크론이 그때만 사용자에게 알린다(소음 없음).
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 0

if pgrep -f 'model_engineer_cycle\.py --run U1' >/dev/null 2>&1; then
    exit 0                      # 이미 실행 중 → 침묵
fi

L=$(cut -d' ' -f1 /proc/loadavg)
NOW=$(date +%H%M)
START=0
REASON=""
if awk -v l="$L" 'BEGIN{exit !(l < 3.0)}'; then
    START=1; REASON="load1=$L < 3.0"
elif [ "$NOW" -ge 2230 ]; then
    START=1; REASON="마감(22:30) 경과 load1=$L → 강제"
fi
[ "$START" -eq 0 ] && exit 0    # 아직 대기 → 침묵

set -a && . ./.env && set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 PROJ_DIR=/home/jhshi/analyist_dd
echo "[U1 기동] $REASON"
rm -f data/reports/me_cycle/running.pid data/reports/me_cycle/state.json
/usr/bin/python3 -u scripts/model_engineer_cycle.py --start U1 --force 2>&1 | head -3
sleep 40
/usr/bin/python3 scripts/model_engineer_cycle.py --tick 2>&1 | head -4
echo "  컨테이너 빌드 프로세스: $(docker top stock_xgboost_ml 2>/dev/null | grep -c '[w]f_label_sweep')개"
