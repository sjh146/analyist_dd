#!/bin/bash
# macro_features 백필 대기 런처 — 이미 도는 supply→financial 체인(04:04 기동)이 끝난 뒤
# macro_features 를 **직렬로** 빌드한다(동시 실행 금지: 4코어·경쟁 구간에서 서로를 죽인다).
# 2026-10-11 04:1x 엔지니어 자율. 재현: bash scripts/run_cg165_macro_wait.sh
#
# ⚠ 자기매칭 함정(실측 2026-10-11 04:0x): `bash -c '...pgrep -f "빌더파일명"...'` 은 그 명령줄
#   자체에 패턴 문자열이 들어 있어 **자기 자신을 매칭**하고 대기 루프에 갇힌다(2시간 상한까지 대기).
#   회피는 대괄호 트릭 하나뿐이라, 아래 bash -c 본문에는 파일명을 **대괄호 형태로만** 쓴다
#   (주석에도 평문 파일명을 쓰면 다시 자기매칭된다).
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
    if ! pgrep -f "[b]uild_supply_market_features.py" >/dev/null 2>&1 \
       && ! pgrep -f "[b]uild_financial_ratio_features.py" >/dev/null 2>&1; then
      break
    fi
    sleep 30
  done
  echo "[cg165-macro] build $(date -Is)"
  /usr/bin/python3 scripts/build_macro_features.py; echo "[cg165-macro] macro rc=$?"
  echo "[cg165-macro] done $(date -Is)"
' > "$LOG" 2>&1 < /dev/null &
echo "$LOG"
