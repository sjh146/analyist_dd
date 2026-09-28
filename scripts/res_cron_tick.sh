#!/usr/bin/env bash
# res_cron_tick.sh — 크론이 부르는 얇은 래퍼(리서처 사이클).
#
# WHY 이 파일이 필요했나: quant-researcher-monitor 크론 잡이 `res_cron_tick.sh` 를
# 가리키는데 **파일이 없었다**(2026-09-28 확인: scripts/res_cron_tick.sh 부재).
# 그래서 리서처의 장외 자율 틱은 돌지 않았고, 크론 잡만 'scheduled' 로 남아 있었다.
# me_cron_tick.sh 와 같은 규약(비블로킹·stdout 전달)으로 맞춘다.
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1
exec /usr/bin/python3 scripts/researcher_cycle.py --tick
