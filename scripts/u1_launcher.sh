#!/usr/bin/env bash
# U1 대기형 런처 — 저녁 파이프라인(챔피언 재학습)이 끝나 부하가 내려가면 U1 을 시작한다.
#
# WHY(2026-09-25 실측): U1 이 `부하 과다 load1=5.64 > 3.5` 로 시작 보류(rc=3)됐다. 저녁 파이프라인의
# 챔피언 재학습이 CPU 를 쓰는 동안 강제로 시작하면 둘 다 느려진다. 반대로 가드가 풀릴 때까지
# 아무도 안 띄우면 밤이 그냥 날아간다(크론 tick 은 매시 1회라 타이밍이 맞지 않을 수 있다).
# 그래서 **부하를 지켜보다가** 임계 이하가 되면 시작하고, 90분이 지나도 안 내려가면 강제 시작한다.
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1
set -a && . ./.env && set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 PROJ_DIR=/home/jhshi/analyist_dd

LOG=/tmp/u1_launcher.log
: > "$LOG"
echo "[$(date '+%T')] U1 대기형 런처 시작 (임계 load1<3.0, 최대 90분 대기 후 강제)" >> "$LOG"

DEADLINE=$(( $(date +%s) + 5400 ))   # 90분
while :; do
    if pgrep -f 'model_engineer_cycle\.py --run U1' >/dev/null 2>&1; then
        echo "[$(date '+%T')] U1 이미 실행 중 — 런처 종료" >> "$LOG"; break
    fi
    L=$(cut -d' ' -f1 /proc/loadavg)
    if awk -v l="$L" 'BEGIN{exit !(l < 3.0)}'; then
        echo "[$(date '+%T')] load1=$L < 3.0 → U1 시작" >> "$LOG"
        /usr/bin/python3 -u scripts/model_engineer_cycle.py --start U1 >> "$LOG" 2>&1
        break
    fi
    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
        echo "[$(date '+%T')] 90분 경과(load1=$L) → 강제 시작" >> "$LOG"
        /usr/bin/python3 -u scripts/model_engineer_cycle.py --start U1 --force >> "$LOG" 2>&1
        break
    fi
    echo "[$(date '+%T')] load1=$L — 대기(5분)" >> "$LOG"
    sleep 300
done
sleep 45
echo "--- 기동 확인 ---" >> "$LOG"
/usr/bin/python3 scripts/model_engineer_cycle.py --tick >> "$LOG" 2>&1
echo "  컨테이너 빌드 프로세스: $(docker top stock_xgboost_ml 2>/dev/null | grep -c '[w]f_label_sweep')개" >> "$LOG"
tail -12 "$LOG"
