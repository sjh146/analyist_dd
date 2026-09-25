#!/usr/bin/env bash
# U1 대기형 런처 v2 — 부하가 내려가면 U1(150종목 패널 빌드, 약 11시간)을 시작한다.
#
# WHY v2 (2026-09-25 21:00 실측): v1 은 90분 뒤 `--start U1 --force` 로 강제 시작했지만 그 시각
# load1=7.1(postgres 291% CPU + yfinance 수집 + 챔피언 재학습)이라 드라이버가 **rc=3 으로 거부**했고
# 런처는 그대로 종료했다 → 크론 틱이 매시 재시도하지만 같은 가드에 막히면 11시간 빌드가 밤새
# 시작되지 못한다("밤이 그냥 날아간다"). 두 곳을 고쳤다:
#   ① 드라이버: --force 는 **장외에서만** 부하 가드를 무시한다(장중·타 역할·사이클 중은 여전히 차단).
#      진짜 직렬화 대상은 '우리 학습 두 개'이고 그건 force 경로에서도 먼저 검사된다.
#   ② 런처 v2: 하루 06:00 까지 5~10분 간격으로 재시도하고, 시작 후 **컨테이너에서 실제로
#      wf_label_sweep 이 도는지** 확인한다(자기신고 금지). 확인 실패면 계속 재시도한다.
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1
set -a && . ./.env && set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 PROJ_DIR=/home/jhshi/analyist_dd

LOG=/home/jhshi/analyist_dd/data/reports/me_cycle/u1_launcher.log
mkdir -p "$(dirname "$LOG")"
say() { echo "[$(date '+%m-%d %T')] $*" >> "$LOG"; }

START=$(date +%s)
FORCE_AT=$((START + 5400))                       # 90분 뒤부터 --force 허용(장외 한정)
HARD=$(date -d 'today 06:00' +%s)
[ "$HARD" -le "$START" ] && HARD=$(date -d 'tomorrow 06:00' +%s)

cycle_running() { pgrep -f 'model_engineer_cycle\.py --run U1' >/dev/null 2>&1; }
build_running() { docker top stock_xgboost_ml 2>/dev/null | grep -q '[w]f_label_sweep'; }

say "U1 대기형 런처 v2 시작 (임계 load1<3.0 · 90분 뒤 --force · 하드 데드라인 $(date -d "@$HARD" '+%m-%d %H:%M'))"
while :; do
    now=$(date +%s)
    if cycle_running; then say "U1 사이클 실행 중 확인 — 런처 종료"; exit 0; fi
    L=$(cut -d' ' -f1 /proc/loadavg)
    if awk -v l="$L" 'BEGIN{exit !(l < 3.0)}'; then
        say "load1=$L < 3.0 → U1 시작"
        /usr/bin/python3 -u scripts/model_engineer_cycle.py --start U1 >> "$LOG" 2>&1
    elif [ "$now" -ge "$FORCE_AT" ]; then
        say "load1=$L · 90분 경과 → --force 시작(장외 한정)"
        /usr/bin/python3 -u scripts/model_engineer_cycle.py --start U1 --force >> "$LOG" 2>&1
    else
        say "load1=$L — 대기(5분)"
        sleep 300; continue
    fi
    for i in 1 2 3 4 5 6; do                       # 기동 확인(최대 2분)
        sleep 20
        if cycle_running || build_running; then
            say "기동 확인 — 컨테이너 빌드 프로세스 $(docker top stock_xgboost_ml 2>/dev/null | grep -c '[w]f_label_sweep')개"
            exit 0
        fi
    done
    say "기동 실패(사이클·빌드 프로세스 미확인) — 10분 뒤 재시도"
    [ "$(date +%s)" -ge "$HARD" ] && { say "하드 데드라인 도달 — 런처 종료(다음 크론 틱이 이어받는다)"; exit 1; }
    sleep 600
done
