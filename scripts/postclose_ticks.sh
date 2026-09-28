#!/usr/bin/env bash
# postclose_ticks.sh — 장 마감 직후 리서처·엔지니어 장외 틱을 연속 실행한다(평일 15:35).
#
# WHY 12/14시가 아니라 15:35인가: 두 역할의 공용 가드가 **장중(09:00~15:30) 시작 금지**다
# (매매 경로 보호 — CPU 를 오래 쓰는 학습이 장중에 돌면 안 된다). 그래서 12:00·14:00 에 틱을
# 넣어도 "대기: 장중" 만 찍고 끝난다. 대신 마감 직후로 앞당기면 **그날 데이터로 바로** 일을
# 시작해, 기존 스케줄(엔지니어 16~07시 매시 · 리서처 16,18,20,22,0,2,4,6시)이 남기던
# 07~16시 공백을 줄인다(실측 2026-09-28: 리서처 마지막 원장 06:01 → 14:37 까지 두 역할 유휴).
#
# 순서는 계약대로 리서처(데이터) → 엔지니어(검증). 각 틱은 비블로킹이라 이 스크립트는
# 수십 초 안에 끝난다(오래 걸리는 실험은 구동기가 nohup 으로 던진다).
set -uo pipefail
cd /home/jhshi/analyist_dd || exit 1

echo "=== researcher tick ($(date '+%F %T')) ==="
/usr/bin/python3 scripts/researcher_cycle.py --tick 2>&1 | tail -40
echo
echo "=== model engineer tick ($(date '+%F %T')) ==="
/usr/bin/python3 scripts/model_engineer_cycle.py --tick 2>&1 | tail -40
echo
echo "=== trader tick ($(date '+%F %T')) ==="
/usr/bin/python3 scripts/trader_cycle.py --tick 2>&1 | tail -25
