#!/usr/bin/env bash
# me_cron_tick.sh — 크론이 부르는 얇은 래퍼.
# 하는 일은 하나뿐이다: 사이클 구동기의 틱을 실행하고 그 stdout 을 크론 에이전트에 넘긴다.
# 오래 걸리는 실험은 구동기가 nohup 으로 던지므로 이 스크립트는 **항상 수 초 안에 끝난다**.
# (크론 틱이 학습을 붙잡으면 다음 틱이 밀린다 — 그래서 비블로킹이 규칙이다.)
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1
exec /usr/bin/python3 scripts/model_engineer_cycle.py --tick
