#!/bin/bash
# CG165 백필 런처 — 피처 원천 신선도 복구(배선된 원천의 정지).
# 2026-10-11 04:0x 엔지니어 자율(장외). 실측: supply_market_features·financial_ratio_features
# 2026-09-29 정지(6거래일 결손) · macro_features 2026-09-28 정지(7거래일) — 세 빌더 모두
# 스케줄이 없다(미배선 스윕은 '배선된' 원천을 보지 않아 12일간 무보고).
# 재현: bash scripts/run_cg165_backfill.sh
# setsid 로 띄워 Hermes 세션 정리(SIGKILL)와 무관하게 완주시킨다.
set -u
cd /home/jhshi/analyist_dd || exit 1
set -a; . ./.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
TS=$(date +%Y%m%d_%H%M)
LOG="$PWD/data/reports/cg165_backfill_${TS}.log"
setsid nohup bash -c '
  cd /home/jhshi/analyist_dd
  echo "[cg165] start $(date -Is)"
  /usr/bin/python3 scripts/build_supply_market_features.py; echo "[cg165] supply rc=$?"
  /usr/bin/python3 scripts/build_financial_ratio_features.py; echo "[cg165] financial rc=$?"
  /usr/bin/python3 scripts/build_macro_features.py; echo "[cg165] macro rc=$?"
  echo "[cg165] done $(date -Is)"
' > "$LOG" 2>&1 < /dev/null &
echo "$LOG"
