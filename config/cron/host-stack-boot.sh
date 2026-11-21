#!/bin/bash
# /usr/local/bin/stack-boot.sh 패치(2026-11-21): ① 추가 스왑 보장 ② 게이트웨이 생존 확인을 1분 주기로
# 근거: 게이트웨이 liveness watchdog 이 exit 75 로 죽은 뒤 다음 스택 점검(최대 5분)까지 크론 실행이 끊겼다.
set -u
LOG=/var/log/analyist-stack-boot.log
log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
[ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 1048576 ] && tail -500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"

changed=0

# 0) 추가 스왑(4GB) — 없으면 만든다. ML 주기 실행 때 기본 2GB 스왑이 꽉 차 게이트웨이가 굶는 것을 막는다.
if ! swapon --show=NAME --noheadings 2>/dev/null | grep -qx /swapfile2; then
  /usr/local/bin/ensure-swap.sh >/dev/null 2>&1 || true
  if swapon --show=NAME --noheadings 2>/dev/null | grep -qx /swapfile2; then
    log "swap added: $(free -m | awk '/Swap:/{print $2"MB total"}')"
    changed=1
  else
    log "swap FAILED"
  fi
fi

# 1) cron
if ! pgrep -x cron >/dev/null 2>&1; then
  service cron start >/dev/null 2>&1
  pgrep -x cron >/dev/null 2>&1 && { log "cron started"; changed=1; } || log "cron FAILED"
fi

# 2) docker
if ! pgrep -x dockerd >/dev/null 2>&1; then
  /usr/local/bin/start-docker.sh >/dev/null 2>&1 || true
  pgrep -x dockerd >/dev/null 2>&1 && { log "dockerd started"; changed=1; } || log "dockerd FAILED"
fi

# 3) feed 서버 (trader-agent 가 읽는 screener_latest.json, 0.0.0.0:8090)
if ! pgrep -f 'scripts/feed_server.py' >/dev/null 2>&1; then
  su - jhshi -c '/home/jhshi/cron/start-feed.sh' >/dev/null 2>&1 || true
  pgrep -f 'scripts/feed_server.py' >/dev/null 2>&1 && { log "feed server started"; changed=1; } || log "feed server FAILED"
fi

# 4) Hermes 게이트웨이 (역할 크론·알림의 실행 주체)
#    재기동 시 그 시점의 메모리를 함께 남긴다 — exit 75 원인이 메모리 고갈인지 로그만으로 판별하기 위해서다.
if ! pgrep -f 'hermes gateway run' >/dev/null 2>&1 && ! pgrep -f 'hermes-agent/venv/bin/python.*gateway' >/dev/null 2>&1; then
  mem=$(free -m | awk '/Mem:/{printf "avail=%sMB", $7}')
  swp=$(free -m | awk '/Swap:/{printf "swap_used=%sMB/%sMB", $3, $2}')
  su - jhshi -c 'cd /home/jhshi && setsid nohup /home/jhshi/.local/bin/hermes gateway run >> /home/jhshi/.hermes/logs/gateway.log 2>&1 &' >/dev/null 2>&1 || true
  sleep 6
  if pgrep -f 'gateway run' >/dev/null 2>&1; then log "gateway started ($mem $swp)"; changed=1; else log "gateway FAILED ($mem $swp)"; fi
fi

# 5) 요약(변화가 있을 때만 기록 — 1분 주기 로그 폭주 방지)
if [ "$changed" = "1" ]; then
  log "containers up: $(docker ps -q 2>/dev/null | wc -l)/17"
fi
exit 0
