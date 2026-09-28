#!/usr/bin/env bash
# tr_cron_tick.sh — 크론이 부르는 얇은 래퍼(트레이더 사이클).
# 하는 일은 하나뿐이다: 사이클 구동기의 틱을 실행하고 그 stdout 을 크론 에이전트에 넘긴다.
# 틱은 측정·검증·환류만 하고 **주문을 내지 않는다**(보드 §권한) — 그래서 항상 수 초 안에 끝난다.
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1
exec /usr/bin/python3 scripts/trader_cycle.py --tick
