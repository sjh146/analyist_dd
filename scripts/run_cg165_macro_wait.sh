#!/bin/bash
# macro_features 백필 대기 런처 — 이미 도는 supply→financial 체인(04:04 기동)이 끝난 뒤
# macro_features 를 **직렬로** 빌드한다(동시 실행 금지: 4코어·경쟁 구간에서 서로를 죽인다).
# 2026-10-11 04:1x 엔지니어 자율. 재현: bash scripts/run_cg165_macro_wait.sh
set -u
cd /home/jhshi/analyist_dd || exit 1
set -a; . ./.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
TS=$(date +%Y%m%d_%H%M)
LOG="$PWD/data/reports/cg165_macro_${TS}.log"
setsid nohup bash -c '
  cd /home/jhshi/analyist_dd
  echo "[cg165-macro] wait start $(date -Is)"
  for i in $(seq 1 240); do
    if ! pgrep -f "build_supply_market_features.py" >/dev/null 2>&1 \
       && ! pgrep -f "build_financial_ratio_features.py" >/dev/null 2>&1; then
      break
    fi
    sleep 30
  done
  echo "[cg165-macro] build $(date -Is)"
  /usr/bin/python3 scripts/build_macro_features.py; echo "[cg165-macro] macro rc=$?"
  echo "[cg165-macro] done $(date -Is)"
' > "$LOG" 2>&1 < /dev/null &
echo "$LOG"
