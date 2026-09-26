#!/usr/bin/env bash
# U3 대기형 런처 — 부하가 내려가면 U3(확장 이력 패널 빌드 + 로버스트 측정)을 시작한다.
#
# WHY (2026-09-26 실측): 틱은 매시 1회이고 그 **순간** load1 이 3.5 를 넘으면 그냥 대기한다.
# 오늘은 WSL 재부팅(18:44) 직후 다른 역할의 수집기 + postgres 병렬워커가 load1=11 을 만들었고,
# 18:45 틱은 "부하 과다"로 아무것도 시작하지 못했다. 밤이 통째로 비는 것을 막으려면 5분 간격
# 재시도가 필요하다(u1_launcher.sh 와 같은 이유·같은 구조).
#   ① load1 < 3.0 이면 시작  ② 60분 경과 후부터 --force(드라이버가 **장외에서만** 허용)
#   ③ 시작 직후 컨테이너에서 실제 빌드 프로세스를 확인(자기신고 금지) ④ 하드 데드라인 09:00
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1

LOG=/home/jhshi/analyist_dd/data/reports/me_cycle/u3_launcher.log
LOCK=/home/jhshi/analyist_dd/data/reports/me_cycle/u3_launcher.pid
mkdir -p "$(dirname "$LOG")"
say() { echo "[$(date '+%m-%d %T')] $*" >> "$LOG"; }

# 단일 인스턴스 가드(pgrep 자기매칭 교훈): pidfile 의 pid 가 **살아있고 cmdline 이 런처**일 때만
# '이미 실행 중'으로 본다. cmdline 검사가 없으면 pid 재사용·유령 pidfile 에 런처가 조용히 죽는다.
if [ -f "$LOCK" ]; then
    op=$(cat "$LOCK" 2>/dev/null || true)
    if [ -n "$op" ] && [ -d "/proc/$op" ] && tr '\0' ' ' < "/proc/$op/cmdline" 2>/dev/null | grep -q 'u3_launcher\.sh'; then
        say "이미 런처 실행 중(pid=$op) — 종료"
        exit 0
    fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

START=$(date +%s)
FORCE_AT=$((START + 3600))                        # 60분 뒤부터 --force 허용(장외 한정)
HARD=$(date -d 'today 09:00' +%s)
[ "$HARD" -le "$START" ] && HARD=$(date -d 'tomorrow 09:00' +%s)

# 자기 매칭 방지: 패턴은 브래킷으로 쪼개 쓰고(--run U3), 래퍼 자신의 커맨드라인에는
# --start U3 만 들어간다.
cycle_running() { pgrep -f 'model_engineer_cycle\.py --run U3' >/dev/null 2>&1; }
build_running() { docker top stock_xgboost_ml 2>/dev/null | grep -q '[w]f_label_sweep'; }

say "U3 대기형 런처 시작 (임계 load1<3.0 · 60분 뒤 --force · 하드 데드라인 $(date -d "@$HARD" '+%m-%d %H:%M'))"
while :; do
    now=$(date +%s)
    if cycle_running; then say "U3 사이클 실행 중 확인 — 런처 종료"; exit 0; fi
    L=$(cut -d' ' -f1 /proc/loadavg)
    if awk -v l="$L" 'BEGIN{exit !(l < 3.0)}'; then
        say "load1=$L < 3.0 → U3 시작"
        /usr/bin/python3 -u scripts/model_engineer_cycle.py --start U3 >> "$LOG" 2>&1
    elif [ "$now" -ge "$FORCE_AT" ]; then
        say "load1=$L · 60분 경과 → --force 시작(장외 한정)"
        /usr/bin/python3 -u scripts/model_engineer_cycle.py --start U3 --force >> "$LOG" 2>&1
    else
        say "load1=$L — 대기(5분)"
        sleep 300; continue
    fi
    for i in 1 2 3 4 5 6; do                      # 기동 확인(최대 2분)
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
